"""RED-first tests for the Block 4 dissent contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from intelligence import dissent
from schema.events import Axis, Event, EventKind, ScreeningResult, Source

T0 = datetime(2025, 5, 6, tzinfo=timezone.utc)
COMPANY_ID = uuid4()
OTHER_COMPANY_ID = uuid4()


def _screening() -> ScreeningResult:
    return ScreeningResult(
        company_id=COMPANY_ID,
        as_of=T0,
        founder=Axis(score=0.8, trend=0.1, confidence=0.7),
        market=Axis(score=0.3, trend=-0.1, confidence=0.6),
        idea_vs_market=Axis(score=0.6, trend=0.0, confidence=0.8),
    )


def _event(
    marker: str,
    *,
    company_id=COMPANY_ID,
    observed_at=T0,
    integrity_flags: list[str] | None = None,
) -> Event:
    return Event(
        company_id=company_id,
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        observed_at=observed_at,
        payload={"claim": marker},
        evidence_span="slide 5",
        integrity_flags=integrity_flags or [],
    )


def _answer(event_id=None) -> dict:
    answer = {
        "bear_case": "Demand may be too narrow to support the proposed approach.",
        "weakest_evidence": ["The usage claim has only one dated receipt."],
        "load_bearing_claim": "Repeat usage continues after the initial trial.",
        "bear_axes": {"founder": -2, "market": 0.9, "idea_vs_market": 0.4},
    }
    if event_id is not None:
        answer["evidence_event_ids"] = [str(event_id)]
    return answer


def test_generate_from_evidence_filters_events_and_routes_text_only_as_untrusted() -> None:
    included = _event("included marker")
    future = _event("future marker", observed_at=T0 + timedelta(seconds=1))
    flagged = _event("flagged marker", integrity_flags=["injection_stripped"])
    foreign = _event("foreign marker", company_id=OTHER_COMPANY_ID)
    seen: dict = {}

    def judge(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return _answer(included.event_id)

    memo = dissent.generate_from_evidence(
        COMPANY_ID,
        T0,
        [included, future, flagged, foreign],
        _screening(),
        judge,
    )

    assert "included marker" not in seen["prompt"]
    assert "included marker" in seen["untrusted"]
    assert "future marker" not in seen["untrusted"]
    assert "flagged marker" not in seen["untrusted"]
    assert "foreign marker" not in seen["untrusted"]
    assert seen["json_mode"] is True
    assert memo.company_id == COMPANY_ID
    assert _answer()["bear_case"] in memo.bear_case
    assert _answer()["weakest_evidence"][0] in memo.weakest_evidence[0]
    assert _answer()["load_bearing_claim"] in memo.load_bearing_claim
    assert str(included.event_id) in memo.bear_case


def test_axis_spreads_are_individual_absolute_differences_with_bear_clipping() -> None:
    event = _event("included marker")
    memo = dissent.generate_from_evidence(
        COMPANY_ID,
        T0,
        [event],
        _screening(),
        lambda *args, **kwargs: _answer(event.event_id),
    )
    assert memo.axis_spreads == {
        "founder": pytest.approx(0.8),
        "market": pytest.approx(0.6),
        "idea_vs_market": pytest.approx(0.2),
    }


def test_bear_agent_is_never_shown_the_bull_scores() -> None:
    """axis_spreads is abs(bull - bear). Handing the bull numbers to the bear agent made
    it echo them back, so every spread was structurally 0.0 — rendered in the UI as three
    empty bars, i.e. "bull and bear agree perfectly", the opposite of the truth."""
    seen: dict = {}

    def judge(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return _answer()

    screening = _screening()
    dissent.generate_from_evidence(COMPANY_ID, T0, [_event("marker")], screening, judge)

    blob = f"{seen['prompt']}{seen.get('system', '')}{seen.get('untrusted', '')}"
    for name, axis in (
        ("founder", screening.founder),
        ("market", screening.market),
        ("idea_vs_market", screening.idea_vs_market),
    ):
        assert str(axis.score) not in blob, (
            f"the bull {name} score {axis.score} was handed to the bear agent; it will be "
            f"echoed back and the spread will collapse to zero"
        )


def test_spread_is_nonzero_when_the_bear_disagrees() -> None:
    """The headline number must actually measure disagreement."""
    memo = dissent.generate_from_evidence(
        COMPANY_ID, T0, [_event("marker")], _screening(), lambda *a, **k: _answer()
    )
    assert any(v > 0 for v in memo.axis_spreads.values()), memo.axis_spreads
    assert dissent.uncertainty_from_spread(memo) > 0


@pytest.mark.parametrize(
    "answer",
    [
        RuntimeError("judge unavailable"),
        "not json",
        {},
        {
            "bear_case": "",
            "weakest_evidence": [],
            "load_bearing_claim": "",
            "bear_axes": {},
        },
        {
            "bear_case": "case",
            "weakest_evidence": ["item"],
            "load_bearing_claim": "claim",
            "bear_axes": {"founder": "bad", "market": 0.2, "idea_vs_market": 0.2},
        },
    ],
)
def test_malformed_or_failed_judge_returns_honest_fallback(answer) -> None:
    def judge(*args, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    memo = dissent.generate_from_evidence(COMPANY_ID, T0, [], _screening(), judge)
    assert memo.company_id == COMPANY_ID
    assert memo.bear_case.strip()
    assert memo.weakest_evidence and all(item.strip() for item in memo.weakest_evidence)
    assert memo.load_bearing_claim.strip()
    assert memo.axis_spreads == {
        "founder": pytest.approx(0.3),
        "market": pytest.approx(0.2),
        "idea_vs_market": pytest.approx(0.1),
    }


def test_generate_wrapper_uses_same_scoped_inputs(monkeypatch) -> None:
    events = [_event("included marker")]
    screening = _screening()
    store_calls: list[dict] = []
    screen_calls: list[tuple] = []
    pure_calls: list[tuple] = []

    def judge(*args, **kwargs):
        return _answer(events[0].event_id)

    def fake_events(**kwargs):
        store_calls.append(kwargs)
        return events

    def fake_three_axis(company_id, as_of):
        screen_calls.append((company_id, as_of))
        return screening

    def fake_generate(company_id, as_of, input_events, input_screening, input_judge):
        pure_calls.append((company_id, as_of, input_events, input_screening, input_judge))
        return dissent.AntiMemo(
            company_id=company_id,
            bear_case="case",
            weakest_evidence=["weak receipt"],
            load_bearing_claim="usage repeats",
        )

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr("intelligence.screen.three_axis", fake_three_axis)
    monkeypatch.setattr(dissent, "generate_from_evidence", fake_generate)

    memo = dissent.generate(COMPANY_ID, T0, judge=judge)

    assert store_calls == [{"company_id": COMPANY_ID, "as_of": T0}]
    assert screen_calls == [(COMPANY_ID, T0)]
    assert pure_calls == [(COMPANY_ID, T0, events, screening, judge)]
    assert memo.company_id == COMPANY_ID


# --- the unscorable bull axis -----------------------------------------------------------
# `screen.py` returns score=None for an axis it could not measure. A spread is
# abs(bull - bear), so against a null bull there is NO SUCH QUANTITY. It is undefined,
# not zero — and this file already documents what writing zero costs: axis_spreads
# identically {0.0, 0.0, 0.0}, read by every consumer as "bull and bear agree perfectly".


def _screening_with_unscored_market() -> ScreeningResult:
    return ScreeningResult(
        company_id=COMPANY_ID,
        as_of=T0,
        founder=Axis(score=0.8, trend=0.1, confidence=0.7),
        market=Axis(score=None, trend=None, confidence=0.0, reason="no events to judge"),
        idea_vs_market=Axis(score=0.6, trend=0.0, confidence=0.8),
    )


def test_a_spread_against_an_unscored_bull_axis_is_omitted_not_zero() -> None:
    event = _event("included marker")
    memo = dissent.generate_from_evidence(
        COMPANY_ID,
        T0,
        [event],
        _screening_with_unscored_market(),
        lambda *args, **kwargs: _answer(event.event_id),
    )
    assert "market" not in memo.axis_spreads, (
        "an unmeasured bull axis produced a spread — 0.0 here is the failure this "
        "codebase already shipped once"
    )
    # The axes we DID measure still produce real spreads. Absence is per-axis, not a
    # switch that blanks the whole reading.
    assert memo.axis_spreads == {
        "founder": pytest.approx(0.8),
        "idea_vs_market": pytest.approx(0.2),
    }


def test_the_fallback_also_omits_the_unscored_axis() -> None:
    memo = dissent.generate_from_evidence(
        COMPANY_ID, T0, [], _screening_with_unscored_market(), lambda *a, **k: "not json"
    )
    assert "market" not in memo.axis_spreads
    assert set(memo.axis_spreads) == {"founder", "idea_vs_market"}


def test_a_missing_spread_reads_as_unknown_uncertainty_not_as_agreement() -> None:
    """The whole point of not writing 0.0: an incomplete spread must not be able to
    masquerade as a narrow one, which is what would let 'wide spread -> no-call' fail
    to fire on a company we scored on nothing."""
    memo = dissent.generate_from_evidence(
        COMPANY_ID, T0, [], _screening_with_unscored_market(), lambda *a, **k: "not json"
    )
    assert dissent.uncertainty_from_spread(memo) == dissent.UNKNOWN_UNCERTAINTY
