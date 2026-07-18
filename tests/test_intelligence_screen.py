"""Locked Block 2 contract tests for the three-axis screen."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from intelligence import screen
from schema.events import Axis, Event, EventKind, FounderScore, ScreeningResult, Source

T0 = datetime(2025, 2, 3, tzinfo=timezone.utc)
COMPANY_ID = uuid4()
ENTITY_ID = uuid4()


def _event(*, text: str = "usage grew", observed_at: datetime = T0) -> Event:
    return Event(
        company_id=COMPANY_ID,
        entity_id=ENTITY_ID,
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        observed_at=observed_at,
        payload={"claim": text},
        evidence_span="slide 4",
    )


def _founder_score(*, mu: float = 0.72, band: float = 0.18) -> FounderScore:
    return FounderScore(
        entity_id=ENTITY_ID,
        as_of=T0,
        mu=mu,
        band=band,
        trend=-0.12,
        contributing_event_ids=[uuid4(), uuid4()],
    )


def test_founder_axis_is_only_a_lossless_reshape_with_clipped_confidence() -> None:
    fs = _founder_score()
    axis = screen.founder_axis(fs)

    assert axis.score == fs.mu
    assert axis.trend == fs.trend
    assert axis.confidence == pytest.approx(1 - fs.band)
    assert axis.evidence_event_ids == fs.contributing_event_ids

    assert screen.founder_axis(_founder_score(band=-0.5)).confidence == 1.0
    assert screen.founder_axis(_founder_score(band=1.5)).confidence == 0.0


@pytest.mark.parametrize("axis_fn", [screen.market_axis, screen.idea_vs_market_axis])
def test_judged_axis_clips_ranges_and_filters_receipts(axis_fn) -> None:
    event = _event()
    invented = uuid4()

    def judge(*args, **kwargs):
        return {
            "score": 2.0,
            "trend": -4.0,
            "confidence": 3.0,
            "evidence_event_ids": [str(event.event_id), str(invented)],
            "rationale": "receipt selected",
        }

    axis = axis_fn([event], judge=judge)

    assert axis == Axis(
        score=1.0,
        trend=-1.0,
        confidence=1.0,
        evidence_event_ids=[event.event_id],
    )


@pytest.mark.parametrize("axis_fn", [screen.market_axis, screen.idea_vs_market_axis])
def test_event_text_is_passed_only_as_untrusted_content(axis_fn) -> None:
    marker = "third party text marker"
    event = _event(text=marker)
    seen: dict = {}

    def judge(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return {
            "score": 0.6,
            "trend": 0.2,
            "confidence": 0.7,
            "evidence_event_ids": [str(event.event_id)],
            "rationale": "ok",
        }

    axis_fn([event], judge=judge)

    assert marker not in seen["prompt"]
    assert marker in seen["untrusted"]
    assert seen["json_mode"] is True
    assert seen["tier"] == "fast"


@pytest.mark.parametrize("axis_fn", [screen.market_axis, screen.idea_vs_market_axis])
@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("judge unavailable"),
        "not json",
        {"score": "not numeric"},
        {"evidence_event_ids": ["not-a-uuid"]},
    ],
)
def test_judged_axis_failure_is_uninformative(axis_fn, response) -> None:
    def judge(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    assert axis_fn([_event()], judge=judge) == Axis(
        score=0.5, trend=0.0, confidence=0.0, evidence_event_ids=[]
    )


def test_missing_required_judge_keys_fall_back() -> None:
    axis = screen.market_axis(
        [_event()],
        judge=lambda *args, **kwargs: {"score": 0.9, "trend": 0.8, "confidence": 1.0},
    )
    assert axis == Axis(score=0.5, trend=0.0, confidence=0.0, evidence_event_ids=[])


def test_all_invented_receipts_make_axis_uninformative() -> None:
    axis = screen.market_axis(
        [_event()],
        judge=lambda *args, **kwargs: {
            "score": 1.0,
            "trend": 1.0,
            "confidence": 1.0,
            "evidence_event_ids": [str(uuid4())],
            "rationale": "unsupported",
        },
    )
    assert axis == Axis(score=0.5, trend=0.0, confidence=0.0, evidence_event_ids=[])


def test_integrity_flagged_events_are_not_judged() -> None:
    event = _event()
    event.integrity_flags.append("injection_stripped")
    called = False

    def judge(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    assert screen.market_axis([event], judge=judge) == Axis(
        score=0.5, trend=0.0, confidence=0.0, evidence_event_ids=[]
    )
    assert called is False


@pytest.mark.parametrize("axis_fn", [screen.market_axis, screen.idea_vs_market_axis])
def test_empty_evidence_does_not_call_judge(axis_fn) -> None:
    def judge(*args, **kwargs):
        raise AssertionError("judge must not run")

    assert axis_fn([], judge=judge) == Axis(
        score=0.5, trend=0.0, confidence=0.0, evidence_event_ids=[]
    )


def test_three_axis_scopes_reads_and_uses_founder_filter(monkeypatch) -> None:
    event = _event()
    fs = _founder_score()
    store_calls: list[dict] = []
    founder_calls: list[tuple[UUID, datetime]] = []

    def fake_events(**kwargs):
        store_calls.append(kwargs)
        return [event]

    def fake_founder(entity_id, as_of):
        founder_calls.append((entity_id, as_of))
        return fs

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr("memory.score.founder", fake_founder)
    monkeypatch.setattr(
        screen, "market_axis", lambda events: Axis(score=0.2, trend=0, confidence=1)
    )
    monkeypatch.setattr(
        screen,
        "idea_vs_market_axis",
        lambda events: Axis(score=0.3, trend=0, confidence=1),
    )

    result = screen.three_axis(COMPANY_ID, T0)

    assert store_calls == [{"company_id": COMPANY_ID, "as_of": T0}]
    assert founder_calls == [(ENTITY_ID, T0)]
    assert result.company_id == COMPANY_ID
    assert result.as_of == T0
    assert result.founder == screen.founder_axis(fs)


def test_three_axis_without_entity_uses_uninformative_founder(monkeypatch) -> None:
    event = _event().model_copy(update={"entity_id": None})
    monkeypatch.setattr("memory.store.events", lambda **kwargs: [event])
    monkeypatch.setattr(
        "memory.score.founder", lambda *args: (_ for _ in ()).throw(AssertionError("no entity"))
    )
    monkeypatch.setattr(
        screen, "market_axis", lambda events: Axis(score=0.2, trend=0, confidence=1)
    )
    monkeypatch.setattr(
        screen,
        "idea_vs_market_axis",
        lambda events: Axis(score=0.3, trend=0, confidence=1),
    )

    assert screen.three_axis(COMPANY_ID, T0).founder == Axis(
        score=0.5, trend=0.0, confidence=0.0, evidence_event_ids=[]
    )


def test_rank_key_is_founder_then_fit_then_market() -> None:
    result = ScreeningResult(
        company_id=COMPANY_ID,
        as_of=T0,
        founder=Axis(score=0.7, trend=0, confidence=1),
        market=Axis(score=0.2, trend=0, confidence=1),
        idea_vs_market=Axis(score=0.9, trend=0, confidence=1),
    )
    assert screen.rank_key(result) == (0.7, 0.9, 0.2)
