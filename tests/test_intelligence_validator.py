"""Locked Block 2 contract tests for per-claim validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from core.search import SearchResult
from intelligence import validator
from schema.events import ClaimStatus, ClaimVerdict, Event, EventKind, Source

T0 = datetime(2025, 3, 10, tzinfo=timezone.utc)
COMPANY_ID = uuid4()


def _claim(*, payload: dict | None = None, evidence_span: str | None = "slide 7") -> Event:
    return Event(
        company_id=COMPANY_ID,
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        observed_at=T0,
        payload=payload if payload is not None else {"claim": "revenue reached 40k"},
        evidence_span=evidence_span,
    )


def _result(
    *,
    published_at: str | None = None,
    self_published: bool = False,
    snippet: str = "reported revenue reached 40k",
) -> SearchResult:
    return SearchResult(
        title="report",
        url="https://example.test/report",
        snippet=snippet,
        published_at=published_at,
        self_published=self_published,
    )


@pytest.mark.parametrize(
    ("status", "self_published", "expected"),
    [
        (ClaimStatus.VERIFIED, False, 0.9),
        (ClaimStatus.VERIFIED, True, 0.6),
        (ClaimStatus.CONTRADICTED, False, 0.15),
        (ClaimStatus.UNVERIFIABLE, False, 0.5),
        (ClaimStatus.NOT_ATTEMPTED, False, 0.5),
    ],
)
def test_trust_for_locked_values(status, self_published, expected) -> None:
    assert validator.trust_for(status, self_published=self_published) == expected


def test_empty_results_are_unverifiable_without_calling_judge() -> None:
    claim = _claim()

    def judge(*args, **kwargs):
        raise AssertionError("judge must not run")

    verdict = validator.check_claim(claim, [], judge=judge)
    assert verdict.status is ClaimStatus.UNVERIFIABLE
    assert verdict.trust == 0.5
    assert verdict.claim_asserted_at == T0
    assert verdict.corroborating_url is None
    assert verdict.corroborating_span is None


def test_support_with_receipt_is_verified_and_uses_untrusted_content() -> None:
    claim = _claim()
    result = _result()
    seen: dict = {}

    def judge(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return {
            "relation": "supports",
            "snippet_index": 0,
            "quote": result.snippet,
            "evidence_date": T0.isoformat(),
        }

    verdict = validator.check_claim(claim, [result], judge=judge)

    assert result.snippet not in seen["prompt"]
    assert result.snippet in seen["untrusted"]
    assert verdict.status is ClaimStatus.VERIFIED
    assert verdict.corroborating_url == result.url
    assert verdict.corroborating_span == result.snippet
    assert verdict.trust == 0.9
    assert verdict.claim_asserted_at == T0
    assert verdict.claim_id == claim.event_id


def test_invented_quote_falls_back_to_stored_snippet() -> None:
    claim = _claim()
    result = _result(snippet="stored result text")
    verdict = validator.check_claim(
        claim,
        [result],
        judge=lambda *args, **kwargs: {
            "relation": "supports",
            "snippet_index": 0,
            "quote": "invented words",
            "evidence_date": None,
        },
    )
    assert verdict.status is ClaimStatus.VERIFIED
    assert verdict.corroborating_span == "stored result text"


def test_self_published_support_is_marked_and_has_capped_trust() -> None:
    result = _result(self_published=True)
    verdict = validator.check_claim(
        _claim(),
        [result],
        judge=lambda *args, **kwargs: {
            "relation": "supports",
            "snippet_index": 0,
            "quote": result.snippet,
            "evidence_date": None,
        },
    )
    assert verdict.status is ClaimStatus.VERIFIED
    assert verdict.self_published is True
    assert verdict.trust == 0.6


@pytest.mark.parametrize(
    "answer",
    [
        {"relation": "supports", "snippet_index": None, "quote": "text", "evidence_date": None},
        {"relation": "supports", "snippet_index": 8, "quote": "text", "evidence_date": None},
    ],
)
def test_support_without_resolvable_receipt_is_not_attempted(answer) -> None:
    verdict = validator.check_claim(_claim(), [_result()], judge=lambda *args, **kwargs: answer)
    assert verdict.status is ClaimStatus.NOT_ATTEMPTED
    assert verdict.corroborating_url is None
    assert verdict.corroborating_span is None


def test_later_counter_evidence_is_contradicted() -> None:
    later = T0 + timedelta(days=2)
    result = _result(snippet=f"revenue had not started on {later.isoformat()}")
    verdict = validator.check_claim(
        _claim(),
        [result],
        judge=lambda *args, **kwargs: {
            "relation": "contradicts",
            "snippet_index": 0,
            "quote": "revenue had not started",
            "evidence_date": later.isoformat(),
        },
    )
    assert verdict.status is ClaimStatus.CONTRADICTED
    assert verdict.trust == 0.15
    assert verdict.counter_evidence_at == later
    assert verdict.corroborating_url == result.url


def test_earlier_counter_evidence_is_growth_not_contradiction() -> None:
    earlier = T0 - timedelta(days=30)
    result = _result(snippet=f"revenue had not started on {earlier.isoformat()}")
    verdict = validator.check_claim(
        _claim(),
        [result],
        judge=lambda *args, **kwargs: {
            "relation": "contradicts",
            "snippet_index": 0,
            "quote": "revenue had not started",
            "evidence_date": earlier.isoformat(),
        },
    )
    assert verdict.status is ClaimStatus.UNVERIFIABLE
    assert verdict.counter_evidence_at == earlier
    assert verdict.claim_asserted_at == T0


def test_ungrounded_counter_date_without_publication_is_unverifiable() -> None:
    later = T0 + timedelta(days=2)
    verdict = validator.check_claim(
        _claim(),
        [_result(snippet="revenue had not started")],
        judge=lambda *args, **kwargs: {
            "relation": "contradicts",
            "snippet_index": 0,
            "quote": "revenue had not started",
            "evidence_date": later.isoformat(),
        },
    )
    assert verdict.status is ClaimStatus.UNVERIFIABLE
    assert verdict.counter_evidence_at is None


def test_integrity_flagged_claim_is_not_attempted_without_judging() -> None:
    claim = _claim()
    claim.integrity_flags.append("injection_stripped")

    def judge(*args, **kwargs):
        raise AssertionError("judge must not run")

    assert (
        validator.check_claim(claim, [_result()], judge=judge).status is ClaimStatus.NOT_ATTEMPTED
    )


@pytest.mark.parametrize(
    ("published_at", "expected"),
    [
        ((T0 + timedelta(days=1)).isoformat(), ClaimStatus.CONTRADICTED),
        ((T0 - timedelta(days=1)).isoformat(), ClaimStatus.UNVERIFIABLE),
    ],
)
def test_unknown_evidence_date_falls_back_to_result_publication_date(
    published_at, expected
) -> None:
    verdict = validator.check_claim(
        _claim(),
        [_result(published_at=published_at)],
        judge=lambda *args, **kwargs: {
            "relation": "contradicts",
            "snippet_index": 0,
            "quote": "revenue had not started",
            "evidence_date": None,
        },
    )
    assert verdict.status is expected
    assert verdict.counter_evidence_at == datetime.fromisoformat(published_at)


def test_unrelated_result_is_unverifiable() -> None:
    verdict = validator.check_claim(
        _claim(),
        [_result()],
        judge=lambda *args, **kwargs: {
            "relation": "unrelated",
            "snippet_index": None,
            "quote": None,
            "evidence_date": None,
        },
    )
    assert verdict.status is ClaimStatus.UNVERIFIABLE


@pytest.mark.parametrize("answer", [RuntimeError("unavailable"), "not json", {"relation": "other"}])
def test_judge_failure_or_malformed_response_is_not_attempted(answer) -> None:
    def judge(*args, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert (
        validator.check_claim(_claim(), [_result()], judge=judge).status
        is ClaimStatus.NOT_ATTEMPTED
    )


def test_claim_text_and_source_span_fallbacks() -> None:
    claim = _claim(payload={}, evidence_span="fallback claim text")
    verdict = validator.check_claim(claim, [], judge=lambda *args, **kwargs: {})
    assert verdict.claim_text == "fallback claim text"
    assert verdict.claim_source_span == "fallback claim text"

    claim_with_slide = _claim(payload={"claim": "claim text", "slide": "slide 2"})
    verdict_with_slide = validator.check_claim(
        claim_with_slide, [], judge=lambda *args, **kwargs: {}
    )
    assert verdict_with_slide.claim_text == "claim text"
    assert verdict_with_slide.claim_source_span == "slide 2"


def test_check_claims_uses_scoped_store_search_and_injected_core(monkeypatch) -> None:
    claims = [_claim(), _claim(payload={"claim": "second", "slide": "slide 9"})]
    store_calls: list[dict] = []
    queries: list[str] = []
    checked: list[tuple[Event, list[SearchResult]]] = []
    result = _result()

    monkeypatch.setattr(validator, "utcnow", lambda: T0 + timedelta(days=10))

    def fake_events(**kwargs):
        store_calls.append(kwargs)
        return claims

    def fake_search(query):
        queries.append(query)
        return [result]

    def fake_check(claim, results, judge=validator.llm.complete):
        checked.append((claim, results))
        return ClaimVerdict(
            company_id=COMPANY_ID,
            claim_text=str(claim.payload["claim"]),
            claim_source_span=claim.evidence_span or "",
            status=ClaimStatus.UNVERIFIABLE,
            trust=0.5,
            claim_asserted_at=claim.observed_at,
        )

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr(validator.web_search, "search", fake_search)
    monkeypatch.setattr(validator, "check_claim", fake_check)

    verdicts = validator.check_claims(COMPANY_ID)

    assert store_calls == [
        {
            "company_id": COMPANY_ID,
            "kind": EventKind.DECK_CLAIM,
            "as_of": T0 + timedelta(days=10),
        },
        {
            "company_id": COMPANY_ID,
            "kind": EventKind.VALIDATION_RESULT,
            "as_of": T0 + timedelta(days=10),
        },
    ]
    assert queries == ["revenue reached 40k", "second"]
    assert checked == [(claims[0], [result]), (claims[1], [result])]
    assert len(verdicts) == 2


def test_to_events_has_exact_payload_receipts_and_caller_timestamp() -> None:
    counter_at = T0 + timedelta(days=2)
    verdict = ClaimVerdict(
        company_id=COMPANY_ID,
        claim_text="revenue reached 40k",
        claim_source_span="slide 7",
        status=ClaimStatus.CONTRADICTED,
        trust=0.15,
        corroborating_url="https://example.test/report",
        corroborating_span="revenue had not started",
        self_published=False,
        claim_asserted_at=T0,
        counter_evidence_at=counter_at,
    )
    as_of = T0 + timedelta(days=5)

    events = validator.to_events([verdict], as_of)

    assert len(events) == 1
    event = events[0]
    assert event.kind is EventKind.VALIDATION_RESULT
    assert event.source is Source.VALIDATOR
    assert event.company_id == COMPANY_ID
    assert event.observed_at == as_of
    assert event.evidence_span == verdict.corroborating_span
    assert event.source_url == verdict.corroborating_url
    assert event.payload == {
        "claim_id": str(verdict.claim_id),
        "claim_text": verdict.claim_text,
        "status": verdict.status.value,
        "trust": verdict.trust,
        "self_published": verdict.self_published,
        "claim_asserted_at": T0.isoformat(),
        "counter_evidence_at": counter_at.isoformat(),
    }


def test_to_events_serializes_missing_timestamps_as_null() -> None:
    verdict = ClaimVerdict(
        company_id=COMPANY_ID,
        claim_text="claim",
        claim_source_span="slide",
        status=ClaimStatus.NOT_ATTEMPTED,
        trust=0.5,
    )
    payload = validator.to_events([verdict], T0)[0].payload
    assert payload["claim_asserted_at"] is None
    assert payload["counter_evidence_at"] is None
