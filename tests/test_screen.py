"""Three axes, the green-flag sensor, and the absence classifier. Owner: C.

No live network: core.search, core.llm and memory.score are all mocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from intelligence import flags, gate, screen
from schema.events import (
    Axis,
    Event,
    EventKind,
    FounderScore,
    GateOutcome,
    ScreeningResult,
    Source,
)

COMPANY = uuid4()
ENTITY = uuid4()
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=365)


def _ev(kind: EventKind, source: Source, day: int = 0, **payload) -> Event:
    return Event(
        entity_id=ENTITY,
        company_id=COMPANY,
        kind=kind,
        source=source,
        observed_at=T0 + timedelta(days=day),
        payload=payload,
        evidence_span=payload.get("claim") or payload.get("message"),
    )


def _deck(claim: str, day: int = 0) -> Event:
    return _ev(EventKind.DECK_CLAIM, Source.DECK, day, claim=claim)


# ---------------------------------------------------------------------------
# Three axes are never averaged
# ---------------------------------------------------------------------------


@pytest.fixture
def screened(monkeypatch):
    monkeypatch.setattr(screen, "_company_events", lambda *_: [_deck("we sell to dentists")])
    monkeypatch.setattr(screen, "_company_name", lambda _: "Acme")
    monkeypatch.setattr(screen, "_web_context", lambda _: "market context")
    monkeypatch.setattr(
        screen,
        "_founder_axis",
        lambda *_: Axis(score=0.9, trend=0.1, confidence=0.8, evidence_event_ids=[uuid4()]),
    )
    monkeypatch.setattr(
        screen.llm,
        "complete",
        lambda *a, **k: {"score": 0.1, "trend": -0.5, "confidence": 0.7, "rationale": "dead"},
    )
    return screen.three_axis(COMPANY, AS_OF)


def test_three_axes_are_never_averaged(screened: ScreeningResult) -> None:
    """A great founder on a dead market must stay a DIFFERENT shape from its mean."""
    combined_names = {"overall", "score", "combined", "total", "average", "mean", "composite"}
    assert not combined_names & set(ScreeningResult.model_fields)

    # The distinction survives: the founder axis is high while the market axis is low.
    assert screened.founder.score == 0.9
    assert screened.market.score == 0.1
    assert screened.founder.score != screened.idea_vs_market.score

    # And nothing in the serialized result is the mean of the three.
    mean = (screened.founder.score + screened.market.score + screened.idea_vs_market.score) / 3
    scalars = [v for v in screened.model_dump().values() if isinstance(v, (int, float))]
    assert mean not in scalars


def test_every_axis_carries_receipts(screened: ScreeningResult) -> None:
    for axis in (screened.founder, screened.market, screened.idea_vs_market):
        assert axis.evidence_event_ids, "no score without receipts"


def test_founder_axis_reads_the_founder_score_rather_than_re_deriving_it(monkeypatch) -> None:
    called = {}

    def _founder(entity_id, as_of):
        called["hit"] = entity_id
        return FounderScore(
            entity_id=entity_id, as_of=as_of, mu=0.71, band=0.1, trend=0.03,
            contributing_event_ids=[uuid4()],
        )

    from memory import score

    monkeypatch.setattr(score, "founder", _founder)
    axis = screen._founder_axis([_deck("x")], AS_OF)

    assert called["hit"] == ENTITY
    assert axis.score == pytest.approx(0.71)
    assert axis.trend == pytest.approx(0.03)


def test_founder_axis_degrades_when_the_score_is_not_ready(monkeypatch) -> None:
    from memory import score

    monkeypatch.setattr(score, "founder", lambda *a: (_ for _ in ()).throw(NotImplementedError))
    assert screen._founder_axis([_deck("x")], AS_OF).confidence == 0.0


def test_axis_with_no_context_scores_nothing_rather_than_guessing() -> None:
    assert screen._llm_axis(screen._MARKET_PROMPT, "", []) == screen.UNKNOWN


# ---------------------------------------------------------------------------
# Absence classifier
# ---------------------------------------------------------------------------


def test_designer_with_no_code_is_not_suspicious() -> None:
    events = [
        _deck("We design brand identity systems for independent retail studios"),
        _ev(EventKind.PROFILE_FACT, Source.WEB, 1, bio="designer"),
    ]
    suspicious, why = gate.classify_absence(events)
    assert suspicious is False
    assert "proves nothing" in why


def test_infra_founder_claiming_a_system_with_no_code_is_suspicious() -> None:
    events = [_deck("We built a distributed system with sub-millisecond latency")]
    suspicious, why = gate.classify_absence(events)
    assert suspicious is True
    assert "distributed system" in why


def test_a_build_claim_backed_by_code_is_not_suspicious() -> None:
    events = [
        _deck("We built a distributed system"),
        _ev(EventKind.REPO_ACTIVITY, Source.GITHUB, 5, repo="acme/core"),
    ]
    assert gate.classify_absence(events)[0] is False


def test_no_claims_at_all_is_not_suspicious() -> None:
    """Absence is only ever suspicious relative to what the founder claimed."""
    assert gate.classify_absence([])[0] is False


def test_build_markers_are_word_anchored() -> None:
    """'api' must not fire on 'capital'."""
    assert gate.classify_absence([_deck("We raise capital for therapists")])[0] is False


# ---------------------------------------------------------------------------
# Gate outcomes
# ---------------------------------------------------------------------------


@pytest.fixture
def gated(monkeypatch):
    def _run(events, *, market=0.7, founder_conf=0.8, verdicts=()):
        monkeypatch.setattr(gate, "_company_events", lambda *_: list(events))
        monkeypatch.setattr(
            gate.screen,
            "three_axis",
            lambda *_: ScreeningResult(
                company_id=COMPANY,
                as_of=AS_OF,
                founder=Axis(score=0.7, trend=0.0, confidence=founder_conf),
                market=Axis(score=market, trend=0.0, confidence=0.8),
                idea_vs_market=Axis(score=0.6, trend=0.0, confidence=0.6),
            ),
        )
        monkeypatch.setattr(gate.validator, "check_claims", lambda *a: list(verdicts))
        return gate.evaluate(COMPANY, AS_OF)

    return _run


def test_thin_evidence_routes_to_proof_protocol(gated) -> None:
    d = gated([_deck("We design posters")])
    assert d.outcome == GateOutcome.PROOF_PROTOCOL
    assert d.absence_is_suspicious is False
    assert "PROOF_PROTOCOL" in d.rationale and "never averaged" in d.rationale


def test_suspicious_absence_routes_to_proof_protocol(gated) -> None:
    d = gated([_deck("We built a distributed system")])
    assert d.outcome == GateOutcome.PROOF_PROTOCOL
    assert d.absence_is_suspicious is True


def test_a_confidently_dead_market_is_a_no_call(gated) -> None:
    d = gated([_ev(EventKind.RELEASE, Source.GITHUB, i, repo="a/b") for i in range(6)], market=0.1)
    assert d.outcome == GateOutcome.NO_CALL


def test_sufficient_evidence_proceeds(gated) -> None:
    events = [_ev(EventKind.REPO_ACTIVITY, Source.GITHUB, i, repo="acme/core") for i in range(6)]
    assert gated(events).outcome == GateOutcome.PROCEED


# ---------------------------------------------------------------------------
# Green flags
# ---------------------------------------------------------------------------


def test_rule_count_is_in_the_agreed_range() -> None:
    assert 30 <= len(flags.RULES) <= 50
    assert len({r.id for r in flags.RULES}) == len(flags.RULES)


def test_every_fired_flag_carries_an_evidence_span() -> None:
    history = [
        _ev(EventKind.REPO_ACTIVITY, Source.GITHUB, d, repo="acme/core", message="rewrote the parser")
        for d in (0, 20, 45, 200, 340)
    ]
    events = flags.evaluate(ENTITY, AS_OF, events=history)
    fired = [e for e in events if e.payload.get("rule_id")]

    assert fired
    assert all(e.evidence_span for e in fired)
    assert all(e.kind == EventKind.GREEN_FLAG for e in events)


def test_rollup_carries_all_three_payload_shapes() -> None:
    """The contract with memory/score.py: value, y, AND flags, so neither side guesses."""
    rollup = flags.evaluate(ENTITY, AS_OF, events=[_deck("we do a thing")])[-1]

    assert rollup.payload["rollup"] is True
    assert rollup.payload["value"] == rollup.payload["y"]
    assert len(rollup.payload["flags"]) == len(flags.RULES)
    assert all({"id", "fired", "weight"} <= set(f) for f in rollup.payload["flags"])


def test_per_rule_events_carry_no_scalar_so_they_are_not_double_counted() -> None:
    from memory.score import _derive_y

    events = flags.evaluate(ENTITY, AS_OF, events=[_deck("we do a thing")])
    per_rule = [e for e in events if e.payload.get("rule_id")]

    assert all(_derive_y(e.payload) is None for e in per_rule)
    assert _derive_y(events[-1].payload) is not None


def test_observation_is_a_weighted_yes_rate_with_noise() -> None:
    empty = flags.evaluate(ENTITY, AS_OF, events=[])
    y_empty, r_empty = flags.observation(empty)

    rich = flags.evaluate(
        ENTITY,
        AS_OF,
        events=[
            _ev(EventKind.REPO_ACTIVITY, Source.GITHUB, d, repo="acme/core",
                message="rewrote the parser after the postmortem", returning_users=12,
                unprompted=True, has_tests=True)
            for d in (0, 20, 45, 200, 340)
        ],
    )
    y_rich, r_rich = flags.observation(rich)

    assert 0.0 <= y_empty < y_rich <= 1.0
    assert r_rich < r_empty  # more fired rules == a less noisy reading


def test_observation_works_from_per_rule_events_alone() -> None:
    """D may hand back only the receipts; the denominator is then the full rule set."""
    events = flags.evaluate(ENTITY, AS_OF, events=[_deck("we do a thing")])
    per_rule = [e for e in events if e.payload.get("rule_id")]
    y, _ = flags.observation(per_rule)

    expected = sum(e.payload["weight"] for e in per_rule) / flags.TOTAL_WEIGHT
    assert y == pytest.approx(expected)


def test_evaluate_never_looks_past_as_of() -> None:
    """Invariant #1: the rollup observation cannot be stamped after as_of."""
    rollup = flags.evaluate(ENTITY, AS_OF, events=[_deck("x", day=999)])[-1]
    assert rollup.observed_at <= AS_OF
