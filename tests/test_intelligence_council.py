"""RED-first tests for independent council roles and chair policy."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from intelligence import council
from schema.events import AntiMemo, Axis, Event, EventKind, ScreeningResult, Source

T0 = datetime(2025, 5, 6, tzinfo=timezone.utc)
COMPANY_ID = uuid4()
OTHER_COMPANY_ID = uuid4()


def _screening() -> ScreeningResult:
    return ScreeningResult(
        company_id=COMPANY_ID,
        as_of=T0,
        founder=Axis(score=0.75, trend=0.1, confidence=0.8),
        market=Axis(score=0.45, trend=-0.1, confidence=0.6),
        idea_vs_market=Axis(score=0.6, trend=0.0, confidence=0.7),
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
        evidence_span="slide 6",
        integrity_flags=integrity_flags or [],
    )


def _role_answer(position: str, event_id: UUID, *, confidence: float = 0.7) -> dict:
    return {
        "position": position,
        "argument": "The receipts support this position with remaining uncertainty.",
        "evidence_event_ids": [str(event_id), str(uuid4())],
        "confidence": confidence,
    }


def _chair_answer() -> dict:
    return {
        "decision": "reach_out",
        "disagreements": ["Market timing remains disputed."],
        "load_bearing_question": "Does repeat usage persist?",
        "evidence_that_would_change_decision": "A dated cohort retention series.",
    }


def test_local_models_have_locked_decisions_and_require_exact_three_roles() -> None:
    assert {item.value for item in council.CouncilDecision} == {
        "reach_out",
        "proof_protocol",
        "no_call",
    }
    argument = council.CouncilArgument(
        role="scout",
        position=council.CouncilDecision.REACH_OUT,
        argument="Evidence supports contact.",
        evidence_event_ids=[],
        confidence=0.5,
    )
    anti_memo = AntiMemo(
        company_id=COMPANY_ID,
        bear_case="case",
        weakest_evidence=["receipt"],
        load_bearing_claim="repeat use",
    )
    with pytest.raises(ValueError):
        council.CouncilResult(
            company_id=COMPANY_ID,
            as_of=T0,
            decision=council.CouncilDecision.PROOF_PROTOCOL,
            arguments=[argument],
            disagreements=[],
            load_bearing_question="question",
            evidence_that_would_change_decision="evidence",
            uncertainty_widening=0.2,
            anti_memo=anti_memo,
        )


def test_deliberation_runs_three_roles_then_chair_with_frozen_untrusted_inputs(monkeypatch) -> None:
    included = _event("included marker")
    future = _event("future marker", observed_at=T0 + timedelta(seconds=1))
    flagged = _event("flagged marker", integrity_flags=["review_required"])
    foreign = _event("foreign marker", company_id=OTHER_COMPANY_ID)
    calls: list[tuple[str, dict]] = []

    def judge(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if len(calls) <= 3:
            positions = ["reach_out", "no_call", "proof_protocol"]
            return _role_answer(positions[len(calls) - 1], included.event_id, confidence=4.0)
        return _chair_answer()

    monkeypatch.setattr(
        council.dissent,
        "generate_from_evidence",
        lambda *args, **kwargs: AntiMemo(
            company_id=COMPANY_ID,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
            axis_spreads={"founder": 0.1, "market": 0.2, "idea_vs_market": 0.05},
        ),
    )

    result = council.deliberate_from_evidence(
        COMPANY_ID,
        T0,
        [included, future, flagged, foreign],
        _screening(),
        judge,
    )

    assert len(calls) == 4
    role_calls = calls[:3]
    assert len({kwargs["untrusted"] for _, kwargs in role_calls}) == 1
    for prompt, kwargs in role_calls:
        assert "included marker" not in prompt
        assert "included marker" in kwargs["untrusted"]
        assert "future marker" not in kwargs["untrusted"]
        assert "flagged marker" not in kwargs["untrusted"]
        assert "foreign marker" not in kwargs["untrusted"]
        frozen = json.loads(kwargs["untrusted"])
        assert set(frozen[0]["axes"]) == {"founder", "market", "idea_vs_market"}
    assert len({kwargs["system"] for _, kwargs in role_calls}) == 3
    chair_prompt, chair_kwargs = calls[3]
    assert "included marker" not in chair_prompt
    assert "reach_out" in chair_kwargs["untrusted"]
    assert [argument.role for argument in result.arguments] == [
        "scout",
        "skeptic",
        "bias_auditor",
    ]
    assert all(argument.evidence_event_ids == [included.event_id] for argument in result.arguments)
    assert all(argument.confidence == 1.0 for argument in result.arguments)
    assert result.decision is council.CouncilDecision.PROOF_PROTOCOL
    assert result.disagreements == _chair_answer()["disagreements"]
    assert result.load_bearing_question == _chair_answer()["load_bearing_question"]
    assert (
        result.evidence_that_would_change_decision
        == _chair_answer()["evidence_that_would_change_decision"]
    )
    assert result.uncertainty_widening == 0.25


def test_malformed_role_becomes_neutral_low_confidence(monkeypatch) -> None:
    event = _event("included marker")
    call_count = 0

    def judge(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "not json"
        if call_count <= 3:
            return _role_answer("reach_out", event.event_id)
        return _chair_answer()

    monkeypatch.setattr(
        council.dissent,
        "generate_from_evidence",
        lambda *args, **kwargs: AntiMemo(
            company_id=COMPANY_ID,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
            axis_spreads={"founder": 0.1, "market": 0.1, "idea_vs_market": 0.1},
        ),
    )
    result = council.deliberate_from_evidence(COMPANY_ID, T0, [event], _screening(), judge)
    neutral = result.arguments[0]
    assert neutral.role == "scout"
    assert neutral.position is council.CouncilDecision.PROOF_PROTOCOL
    assert neutral.confidence <= 0.1
    assert neutral.argument.strip()
    assert neutral.evidence_event_ids == []


def test_malformed_chair_falls_back_to_proof_protocol(monkeypatch) -> None:
    event = _event("included marker")
    call_count = 0

    def judge(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return _role_answer("reach_out", event.event_id)
        raise RuntimeError("chair unavailable")

    monkeypatch.setattr(
        council.dissent,
        "generate_from_evidence",
        lambda *args, **kwargs: AntiMemo(
            company_id=COMPANY_ID,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
            axis_spreads={"founder": 0.3, "market": 0.2, "idea_vs_market": 0.1},
        ),
    )
    result = council.deliberate_from_evidence(COMPANY_ID, T0, [event], _screening(), judge)
    assert result.decision is council.CouncilDecision.PROOF_PROTOCOL
    assert result.load_bearing_question.strip()
    assert result.evidence_that_would_change_decision.strip()


def test_receiptless_roles_cannot_authorize_reach_out(monkeypatch) -> None:
    event = _event("included marker")
    call_count = 0

    def judge(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return {
                "position": "reach_out",
                "argument": "unsupported",
                "evidence_event_ids": [str(uuid4())],
                "confidence": 1.0,
            }
        return _chair_answer()

    monkeypatch.setattr(
        council.dissent,
        "generate_from_evidence",
        lambda *args, **kwargs: AntiMemo(
            company_id=COMPANY_ID,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
        ),
    )
    result = council.deliberate_from_evidence(COMPANY_ID, T0, [event], _screening(), judge)
    assert result.decision is council.CouncilDecision.PROOF_PROTOCOL
    assert all(argument.confidence == 0.0 for argument in result.arguments)


def test_public_response_locks_decision_until_dissent_is_viewed(monkeypatch) -> None:
    event = _event("included marker")
    call_count = 0

    def judge(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return _role_answer("reach_out", event.event_id)
        return _chair_answer()

    monkeypatch.setattr(
        council.dissent,
        "generate_from_evidence",
        lambda *args, **kwargs: AntiMemo(
            company_id=COMPANY_ID,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
        ),
    )
    result = council.deliberate_from_evidence(COMPANY_ID, T0, [event], _screening(), judge)
    monkeypatch.setattr(council, "_run", lambda *args, **kwargs: result)
    locked = council.deliberate(COMPANY_ID, T0)
    opened = council.view_dissent(COMPANY_ID, T0)
    assert locked.decision is None and locked.decision_locked_reason
    assert locked.dissent_viewed is False and locked.anti_memo is None
    assert opened.decision is result.decision and opened.decision_locked_reason is None
    assert opened.dissent_viewed is True and opened.anti_memo == result.anti_memo


def test_foreign_or_future_screening_snapshot_is_rejected() -> None:
    screening = _screening().model_copy(update={"company_id": OTHER_COMPANY_ID})
    with pytest.raises(ValueError, match="snapshot"):
        council.deliberate_from_evidence(COMPANY_ID, T0, [], screening, lambda *a, **k: {})


@pytest.mark.parametrize(
    ("spreads", "positions", "expected"),
    [
        ({"founder": 1.7, "market": 0.2, "idea_vs_market": 0.1}, ["reach_out"] * 3, 1.0),
        ({"founder": 0.1, "market": 0.2, "idea_vs_market": 0.05}, ["reach_out"] * 3, 0.2),
        (
            {"founder": 0.1, "market": 0.2, "idea_vs_market": 0.05},
            ["reach_out", "no_call", "reach_out"],
            0.25,
        ),
    ],
)
def test_uncertainty_widening_uses_max_and_disagreement_floor(
    monkeypatch, spreads, positions, expected
) -> None:
    event = _event("included marker")
    call_count = 0

    def judge(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return _role_answer(positions[call_count - 1], event.event_id)
        return _chair_answer()

    monkeypatch.setattr(
        council.dissent,
        "generate_from_evidence",
        lambda *args, **kwargs: AntiMemo(
            company_id=COMPANY_ID,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
            axis_spreads=spreads,
        ),
    )
    result = council.deliberate_from_evidence(COMPANY_ID, T0, [event], _screening(), judge)
    assert result.uncertainty_widening == expected


def test_view_dissent_wrapper_reads_once_and_reuses_as_of(monkeypatch) -> None:
    events = [_event("included marker")]
    screening = _screening()
    store_calls: list[dict] = []
    screen_calls: list[tuple] = []
    pure_calls: list[tuple] = []

    def judge(*args, **kwargs):
        return _chair_answer()

    def fake_events(**kwargs):
        store_calls.append(kwargs)
        return events

    def fake_screen(company_id, as_of):
        screen_calls.append((company_id, as_of))
        return screening

    def fake_pure(company_id, as_of, input_events, input_screening, input_judge):
        pure_calls.append((company_id, as_of, input_events, input_screening, input_judge))
        anti_memo = AntiMemo(
            company_id=company_id,
            bear_case="case",
            weakest_evidence=["receipt"],
            load_bearing_claim="repeat use",
        )
        arguments = [
            council.CouncilArgument(
                role=role,
                position=council.CouncilDecision.PROOF_PROTOCOL,
                argument="uncertain",
                evidence_event_ids=[],
                confidence=0.0,
            )
            for role in ("scout", "skeptic", "bias_auditor")
        ]
        return council.CouncilResult(
            company_id=company_id,
            as_of=as_of,
            decision=council.CouncilDecision.PROOF_PROTOCOL,
            arguments=arguments,
            disagreements=[],
            load_bearing_question="question",
            evidence_that_would_change_decision="new receipt",
            uncertainty_widening=0.0,
            anti_memo=anti_memo,
        )

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr("intelligence.screen.three_axis", fake_screen)
    monkeypatch.setattr(council, "deliberate_from_evidence", fake_pure)

    result = council.view_dissent(COMPANY_ID, T0, judge=judge)

    assert store_calls == [{"company_id": COMPANY_ID, "as_of": T0}]
    assert screen_calls == [(COMPANY_ID, T0)]
    assert pure_calls == [(COMPANY_ID, T0, events, screening, judge)]
    assert result.company_id == COMPANY_ID
    assert result.decision is council.CouncilDecision.PROOF_PROTOCOL
    assert result.dissent_viewed is True
