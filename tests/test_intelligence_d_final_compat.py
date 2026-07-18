"""RED-first tests for the final intelligence-to-application compatibility boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from intelligence import dissent, proof, validator
from schema.events import AntiMemo, ClaimStatus, ClaimVerdict, Event, EventKind, Source

T0 = datetime(2026, 4, 1, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=30)
COMPANY_ID = uuid4()
OTHER_COMPANY_ID = uuid4()
ENTITY_ID = uuid4()
CHALLENGE_ID = uuid4()


def _claim() -> Event:
    return Event(
        entity_id=ENTITY_ID,
        company_id=COMPANY_ID,
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        observed_at=T0,
        payload={"claim": "The product has repeat weekly usage", "slide": "slide 8"},
        evidence_span="slide 8",
    )


def _stored_corroboration() -> Event:
    return Event(
        company_id=COMPANY_ID,
        kind=EventKind.HN_COMMENT,
        source=Source.HN,
        source_url="https://example.test/item/42",
        observed_at=T0 + timedelta(days=5),
        payload={"title": "Usage discussion"},
        evidence_span="Teams reported returning to the product each week.",
    )


def test_as_of_validation_uses_stored_corroboration_and_never_live_web(monkeypatch) -> None:
    claim = _claim()
    receipt = _stored_corroboration()
    reads: list[dict] = []
    appended: list[Event] = []
    checked_results: list = []

    def fake_events(**kwargs):
        reads.append(kwargs)
        kind = kwargs.get("kind")
        if kind == EventKind.DECK_CLAIM:
            return [claim]
        if kind == EventKind.VALIDATION_RESULT:
            return appended
        return [claim, receipt]

    def fake_check(input_claim, results, judge=validator.llm.complete):
        checked_results.extend(results)
        selected = results[0]
        return ClaimVerdict(
            claim_id=input_claim.event_id,
            company_id=COMPANY_ID,
            claim_text=input_claim.payload["claim"],
            claim_source_span=input_claim.payload["slide"],
            status=ClaimStatus.VERIFIED,
            trust=0.9,
            corroborating_url=selected.url,
            corroborating_span=selected.snippet,
            claim_asserted_at=input_claim.observed_at,
        )

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr(
        "memory.store.append", lambda event: appended.append(event) or event.event_id
    )
    monkeypatch.setattr(validator, "check_claim", fake_check)
    monkeypatch.setattr(
        validator.web_search,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no live web at as_of")),
    )
    monkeypatch.setattr(
        validator,
        "utcnow",
        lambda: (_ for _ in ()).throw(AssertionError("caller cutoff must be used")),
    )

    verdicts = validator.check_claims(COMPANY_ID, AS_OF)

    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.status is ClaimStatus.VERIFIED
    assert verdict.corroborating_url == receipt.source_url
    assert verdict.corroborating_span == receipt.evidence_span
    assert len(checked_results) == 1
    assert checked_results[0].url == receipt.source_url
    assert checked_results[0].snippet == receipt.evidence_span
    assert checked_results[0].published_at == receipt.observed_at.isoformat()
    assert all(call["as_of"] == AS_OF for call in reads)
    assert len(appended) == 1
    emitted = appended[0]
    assert emitted.kind is EventKind.VALIDATION_RESULT
    assert emitted.source is Source.VALIDATOR
    assert emitted.company_id == COMPANY_ID
    assert emitted.entity_id == ENTITY_ID
    assert emitted.observed_at == AS_OF
    assert emitted.payload["claim_id"] == str(claim.event_id)


def test_validation_emission_is_deterministic_and_idempotent(monkeypatch) -> None:
    claim = _claim()
    appended: list[Event] = []

    def fake_events(**kwargs):
        if kwargs.get("kind") == EventKind.DECK_CLAIM:
            return [claim]
        if kwargs.get("kind") == EventKind.VALIDATION_RESULT:
            return appended
        return []

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr(
        "memory.store.append", lambda event: appended.append(event) or event.event_id
    )
    monkeypatch.setattr(
        validator.web_search,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no live web at as_of")),
    )

    first = validator.check_claims(COMPANY_ID, AS_OF)
    second = validator.check_claims(COMPANY_ID, AS_OF)

    assert [verdict.model_dump() for verdict in first] == [
        verdict.model_dump() for verdict in second
    ]
    assert len(appended) == 1


def _issued() -> Event:
    return Event(
        entity_id=ENTITY_ID,
        company_id=COMPANY_ID,
        kind=EventKind.PROOF_CHALLENGE_ISSUED,
        source=Source.PROOF_PROTOCOL,
        observed_at=T0,
        payload={
            "challenge_id": str(CHALLENGE_ID),
            "planted_bad_constraint": "Use a fixed limit for every workload.",
        },
    )


def _legacy_trace() -> dict:
    started = T0 + timedelta(minutes=1)

    def at(minutes: int) -> str:
        return (started + timedelta(minutes=minutes)).isoformat()

    return {
        "started_at": started.isoformat(),
        "submitted_at": at(70),
        "questions_asked": ["Which workload should the fixed limit represent?"],
        "pushed_back_on_constraint": True,
        "commits": [
            {"at": at(10), "message": "measurement harness"},
            {"at": at(35), "message": "revise implementation"},
            {"at": at(65), "message": "document assumption"},
        ],
    }


def _install_proof_boundary(monkeypatch) -> None:
    monkeypatch.setattr(proof, "utcnow", lambda: AS_OF)
    monkeypatch.setattr("memory.store.events", lambda **kwargs: [_issued()])
    monkeypatch.setattr(
        proof.llm,
        "complete",
        lambda *args, **kwargs: {
            "works": 0.8,
            "technically_sound": 0.8,
            "ambiguity_handling": 0.8,
            "evidence_span": "artifact",
        },
    )


def _attestation(*, self_reported: bool) -> dict:
    return {
        "challenge_anchored": True,
        "attested_fields": [
            "started_at",
            "submitted_at",
            "commits",
            *([] if self_reported else ["pushed_back_on_constraint"]),
        ],
        "self_reported_fields": ["pushed_back_on_constraint"] if self_reported else [],
        "trust": 0.6 if self_reported else 1.0,
        "demo_seeded": False,
        "note": "Some fields are independently observed." if self_reported else "Observed trace.",
    }


def test_grade_accepts_attestation_and_expected_company_id(monkeypatch) -> None:
    _install_proof_boundary(monkeypatch)
    attestation = _attestation(self_reported=False)

    events = proof.grade(
        CHALLENGE_ID,
        "artifact",
        _legacy_trace(),
        attestation=attestation,
        expected_company_id=COMPANY_ID,
    )

    assert len(events) == 2
    assert all(event.company_id == COMPANY_ID for event in events)
    assert all(event.payload["attestation"] == attestation for event in events)


def test_grade_rejects_expected_company_mismatch(monkeypatch) -> None:
    _install_proof_boundary(monkeypatch)
    with pytest.raises(ValueError, match="company"):
        proof.grade(
            CHALLENGE_ID,
            "artifact",
            _legacy_trace(),
            attestation=_attestation(self_reported=False),
            expected_company_id=OTHER_COMPANY_ID,
        )


def test_d_server_attestation_embedded_in_graded_trace_controls_components(monkeypatch) -> None:
    _install_proof_boundary(monkeypatch)
    trace = _legacy_trace()
    trace["attestation"] = _attestation(self_reported=True)
    behavior = proof.grade(
        CHALLENGE_ID,
        "artifact",
        trace,
        expected_company_id=COMPANY_ID,
    )[1]
    # D performs the single confidence scaling step after C returns the events.
    assert behavior.confidence == 0.95
    assert behavior.payload["components"]["constraint_pushback"] < 1.0
    assert "unattested_trace" in behavior.integrity_flags


def test_seeded_demo_events_are_disclosed_but_not_score_observations(monkeypatch) -> None:
    _install_proof_boundary(monkeypatch)
    events = proof.grade(
        CHALLENGE_ID,
        "artifact",
        {**_legacy_trace(), "seeded": True, "disclosure": "Pre-recorded demonstration."},
        expected_company_id=COMPANY_ID,
    )
    assert all(event.payload["seeded"] is True for event in events)
    assert all(event.payload["disclosure"] for event in events)
    assert all(event.payload["value"] is None and event.payload["y"] is None for event in events)
    assert all(event.confidence == 0.0 for event in events)
    assert all("seeded_demo" in event.integrity_flags for event in events)


def test_self_reported_pushback_cannot_receive_full_attested_credit(monkeypatch) -> None:
    _install_proof_boundary(monkeypatch)
    attested = proof.grade(
        CHALLENGE_ID,
        "artifact",
        _legacy_trace(),
        attestation=_attestation(self_reported=False),
        expected_company_id=COMPANY_ID,
    )[1]
    self_reported = proof.grade(
        CHALLENGE_ID,
        "artifact",
        _legacy_trace(),
        attestation=_attestation(self_reported=True),
        expected_company_id=COMPANY_ID,
    )[1]

    assert attested.payload["components"]["constraint_pushback"] == 1.0
    assert self_reported.payload["components"]["constraint_pushback"] < 1.0
    assert self_reported.payload["value"] < attested.payload["value"]


def _memo(spreads: dict[str, float]) -> AntiMemo:
    return AntiMemo(
        company_id=COMPANY_ID,
        bear_case="The evidence supports a materially weaker case.",
        weakest_evidence=["Repeat usage has limited receipts."],
        load_bearing_claim="Repeat usage persists.",
        axis_spreads=spreads,
    )


def test_uncertainty_is_bounded_and_monotonic() -> None:
    values = [
        dissent.uncertainty_from_spread(
            _memo({"founder": spread, "market": spread, "idea_vs_market": spread})
        )
        for spread in (0.0, 0.1, 0.4, 0.8, 2.0)
    ]
    assert values == sorted(values)
    assert values[0] < values[-1]
    assert all(0.0 <= value <= 1.0 for value in values)


def test_worst_axis_spread_dominates_uncertainty() -> None:
    narrow = dissent.uncertainty_from_spread(
        _memo({"founder": 0.1, "market": 0.1, "idea_vs_market": 0.1})
    )
    one_wide = dissent.uncertainty_from_spread(
        _memo({"founder": 0.1, "market": 0.1, "idea_vs_market": 0.9})
    )
    assert one_wide > narrow
    assert one_wide >= dissent.uncertainty_from_spread(
        _memo({"founder": 0.3, "market": 0.3, "idea_vs_market": 0.3})
    )


def test_empty_spreads_return_neutral_unknown() -> None:
    value = dissent.uncertainty_from_spread(_memo({}))
    assert value == dissent.UNKNOWN_UNCERTAINTY
    assert value == pytest.approx(0.5)


def test_malformed_or_extra_spreads_cannot_dilute_uncertainty() -> None:
    assert dissent.uncertainty_from_spread(_memo({"founder": -1.0})) == 0.5
    baseline = dissent.uncertainty_from_spread(
        _memo({"founder": 0.4, "market": 0.4, "idea_vs_market": 0.4})
    )
    diluted = dissent.uncertainty_from_spread(
        _memo({"founder": 0.4, "market": 0.4, "idea_vs_market": 0.4, "extra": 0.0})
    )
    assert diluted == baseline
