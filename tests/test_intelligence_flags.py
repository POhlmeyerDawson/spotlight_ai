"""RED-phase spec for intelligence/flags.py. Owner: C. See C.md H1-3, SHARED.md Invariant #3.

Each test protects one clause of the flags.py contract:
  - RULES: a table of >=30 immutable, positively-weighted, source-gated rules with no
    pedigree language anywhere in rule_id/question (Invariant #3, enforced by construction).
  - evaluate_events(): pure core (no I/O). Emits exactly one GREEN_FLAG per APPLICABLE rule
    (rule.requires is None, or at least one input event's source is in rule.requires).
    A rule whose required sources are absent from the input is not emitted at all — that's
    the applicability gate that stops a designer with no GitHub from being penalized by
    GitHub-only rules. observed_at is always backend from evidence, never datetime.now(),
    and never exceeds as_of.
  - evaluate(): the thin store-backed wrapper (not exercised beyond its signature — the
    store itself is still a stub owned by A).
  - observation(): reduces a list of GREEN_FLAG events to the (y_t, r_t) pair A's Kalman
    filter consumes. y_t is the fired-weight fraction; r_t is a confidence-scaled noise
    term clipped to [0.02, 0.5]; an empty flag list is an uninformative (0.5, 0.5) prior.

Fixtures are built the way test_schema_invariants.py builds them: explicit tz-aware
datetimes, no reliance on utcnow(), so `as_of` gating is exercised deterministically.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from intelligence.banned import BANNED_TERMS
from intelligence.flags import RULES, evaluate, evaluate_events, observation
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
ENTITY_ID = uuid4()

EXPECTED_PAYLOAD_KEYS = {"rule_id", "question", "weight", "fired", "evidence_event_ids"}


def _event(
    *,
    kind: EventKind,
    source: Source,
    observed_at: datetime,
    payload: dict,
    entity_id: UUID = ENTITY_ID,
    evidence_span: str | None = None,
    confidence: float = 1.0,
) -> Event:
    return Event(
        kind=kind,
        source=source,
        observed_at=observed_at,
        entity_id=entity_id,
        payload=payload,
        evidence_span=evidence_span,
        confidence=confidence,
    )


def _release_event(observed_at: datetime, version: str = "0.1.0") -> Event:
    return _event(
        kind=EventKind.RELEASE,
        source=Source.GITHUB,
        observed_at=observed_at,
        payload={"repo": "x/y", "version": version},
        evidence_span=f"tagged release x/y {version}",
    )


def _proof_behavior_event(observed_at: datetime) -> Event:
    return _event(
        kind=EventKind.PROOF_BEHAVIOR,
        source=Source.PROOF_PROTOCOL,
        observed_at=observed_at,
        payload={"challenged_bad_constraint": True, "asked_clarifying": True, "iteration_count": 4},
        evidence_span="pushed back on the planted constraint and asked a clarifying question",
    )


def _deck_claim_event(observed_at: datetime) -> Event:
    return _event(
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        observed_at=observed_at,
        payload={"claim": "we have 100 users"},
    )


def _flag_event(
    *,
    rule_id: str,
    weight: float,
    fired: bool,
    evidence_event_ids: list[str] | None = None,
    observed_at: datetime = T0,
    confidence: float = 1.0,
) -> Event:
    """A hand-built GREEN_FLAG event, for exercising observation() without RULES."""
    return _event(
        kind=EventKind.GREEN_FLAG,
        source=Source.MANUAL,
        observed_at=observed_at,
        confidence=confidence,
        payload={
            "rule_id": rule_id,
            "question": f"did {rule_id} happen?",
            "weight": weight,
            "fired": fired,
            "evidence_event_ids": evidence_event_ids or [],
        },
    )


def _rules_by_id() -> dict[str, object]:
    return {rule.rule_id: rule for rule in RULES}


# ---------------------------------------------------------------------------
# RULES table shape
# ---------------------------------------------------------------------------


def test_rules_table_has_at_least_30_rules() -> None:
    """C.md commits to 30-50 interpretable rules; fewer than 30 under-covers the space."""
    assert len(RULES) >= 30


def test_every_rule_has_valid_shape() -> None:
    """Every rule needs a real id/question, a positive weight, and a typed requires set."""
    for rule in RULES:
        assert isinstance(rule.rule_id, str) and rule.rule_id
        assert isinstance(rule.question, str) and rule.question
        assert isinstance(rule.weight, (int, float))
        assert float(rule.weight) > 0
        assert rule.requires is None or isinstance(rule.requires, frozenset)
        if rule.requires is not None:
            assert all(isinstance(source, Source) for source in rule.requires)


def test_rule_ids_are_unique() -> None:
    """Duplicate rule_ids would let the same signal get double-counted under two names."""
    ids = [rule.rule_id for rule in RULES]
    assert len(ids) == len(set(ids))


def test_rule_is_frozen_dataclass() -> None:
    """Rules are immutable value objects; mutating one in place would corrupt the shared table."""
    rule = RULES[0]
    with pytest.raises(FrozenInstanceError):
        rule.weight = 999.0  # type: ignore[misc]


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_no_rule_text_contains_banned_term(term: str) -> None:
    """Invariant #3: gate logic is substance-only, never pedigree, by construction."""
    pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    offenders = [
        rule.rule_id
        for rule in RULES
        if pattern.search(rule.rule_id) or pattern.search(rule.question)
    ]
    assert not offenders, f"Pedigree term {term!r} found in rules {offenders}"


# ---------------------------------------------------------------------------
# evaluate_events(): applicability gating + firing
# ---------------------------------------------------------------------------


def test_release_events_fire_a_github_shipping_rule() -> None:
    """Two shipped releases, months apart, must trip at least one shipping-tuned GitHub rule."""
    events = [_release_event(T0, "0.1.0"), _release_event(T0 + timedelta(days=31), "0.2.0")]
    as_of = T0 + timedelta(days=60)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    rules_by_id = _rules_by_id()
    fired_github_rules = [
        flag
        for flag in flags
        if flag.payload["fired"]
        and rules_by_id[flag.payload["rule_id"]].requires is not None
        and Source.GITHUB in rules_by_id[flag.payload["rule_id"]].requires
    ]
    assert fired_github_rules, "no GitHub-gated rule fired on two spaced releases"


def test_proof_behavior_event_fires_a_proof_rule() -> None:
    """A founder who challenges the planted bad constraint must trip a proof-protocol rule."""
    events = [_proof_behavior_event(T0)]
    as_of = T0 + timedelta(days=1)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    rules_by_id = _rules_by_id()
    fired_proof_rules = [
        flag
        for flag in flags
        if flag.payload["fired"]
        and rules_by_id[flag.payload["rule_id"]].requires is not None
        and Source.PROOF_PROTOCOL in rules_by_id[flag.payload["rule_id"]].requires
    ]
    assert fired_proof_rules, "no proof-protocol-gated rule fired on a challenged constraint"


def test_deck_only_entity_excludes_github_required_rules() -> None:
    """A designer with no GitHub must not be penalized by GitHub-only rules — applicability gating.

    Rules gated on a *combination* of sources (e.g. deck-claim-vs-code) are still applicable
    when only one member of that combination is present; the gate this test protects is the
    exclusively-GitHub rule that has no business firing — or even being listed — for a
    founder with no GitHub footprint at all.
    """
    events = [_deck_claim_event(T0)]
    as_of = T0 + timedelta(days=1)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    rules_by_id = _rules_by_id()
    github_only_flags = [
        flag
        for flag in flags
        if rules_by_id[flag.payload["rule_id"]].requires == frozenset({Source.GITHUB})
    ]
    assert github_only_flags == []


def test_evaluate_events_is_deterministic() -> None:
    """Same inputs must produce the same rule_ids and fired values, or A's filter is unstable."""
    events = [_release_event(T0)]
    as_of = T0 + timedelta(days=1)

    first = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)
    second = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    shape = lambda flags: [(flag.payload["rule_id"], flag.payload["fired"]) for flag in flags]  # noqa: E731
    assert shape(first) == shape(second)


def test_flag_events_have_expected_kind_and_entity_id() -> None:
    """Every emitted flag must be a GREEN_FLAG stamped with the entity it was evaluated for."""
    events = [_release_event(T0), _release_event(T0 + timedelta(days=31), "0.2.0")]
    as_of = T0 + timedelta(days=60)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    assert flags
    assert all(flag.kind == EventKind.GREEN_FLAG for flag in flags)
    assert all(flag.entity_id == ENTITY_ID for flag in flags)


def test_events_from_another_entity_cannot_fire_flags() -> None:
    """Evidence is never relabeled onto a different founder, even with a mixed input list."""
    other_entity = uuid4()
    events = [
        _event(
            kind=EventKind.PROOF_BEHAVIOR,
            source=Source.PROOF_PROTOCOL,
            observed_at=T0,
            entity_id=other_entity,
            payload={"challenged_bad_constraint": True},
        )
    ]

    assert evaluate_events(events, entity_id=ENTITY_ID, as_of=T0 + timedelta(days=1)) == []


def test_integrity_flagged_text_cannot_fire_keyword_rules() -> None:
    """Quarantined text cannot turn an integrity strike into positive scoring evidence."""
    event = _event(
        kind=EventKind.HN_POST,
        source=Source.HN,
        observed_at=T0,
        payload={"text": "postmortem lessons learned benchmark latency"},
    )
    event.integrity_flags.append("injection_stripped")

    assert evaluate_events([event], entity_id=ENTITY_ID, as_of=T0 + timedelta(days=1)) == []


def test_repeat_rule_receipts_include_time_boundaries_for_unsorted_input() -> None:
    """A cadence flag cites the observations that establish its full time span."""
    early = _release_event(T0)
    late = _release_event(T0 + timedelta(days=40), "0.2.0")

    flags = evaluate_events([late, early], entity_id=ENTITY_ID, as_of=late.observed_at)
    repeat = next(flag for flag in flags if flag.payload["rule_id"] == "repeat_shipper")

    assert repeat.payload["fired"] is True
    assert set(repeat.payload["evidence_event_ids"]) == {str(early.event_id), str(late.event_id)}
    assert repeat.observed_at == late.observed_at


def test_flag_payload_has_exact_key_set() -> None:
    """Payload shape is A's contract; extra or missing keys break the observation() reducer."""
    events = [_release_event(T0), _proof_behavior_event(T0 + timedelta(days=1))]
    as_of = T0 + timedelta(days=2)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    assert flags
    for flag in flags:
        assert set(flag.payload.keys()) == EXPECTED_PAYLOAD_KEYS


def test_flag_observed_at_never_exceeds_as_of() -> None:
    """observed_at must never leak beyond as_of — that's how the backtest gets poisoned."""
    events = [_release_event(T0), _release_event(T0 + timedelta(days=31), "0.2.0")]
    as_of = T0 + timedelta(days=45)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    assert flags
    assert all(flag.observed_at <= as_of for flag in flags)


def test_fired_flag_observed_at_and_span_match_its_evidence() -> None:
    """A fired flag's observed_at/evidence_span must trace to the events it cites, never now()."""
    events = [_release_event(T0), _release_event(T0 + timedelta(days=31), "0.2.0")]
    as_of = T0 + timedelta(days=60)
    events_by_id = {event.event_id: event for event in events}

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    fired_with_evidence = [
        flag for flag in flags if flag.payload["fired"] and flag.payload["evidence_event_ids"]
    ]
    assert fired_with_evidence, "expected at least one fired flag backed by evidence"
    for flag in fired_with_evidence:
        cited = [events_by_id[UUID(eid)] for eid in flag.payload["evidence_event_ids"]]
        assert flag.observed_at == max(event.observed_at for event in cited)
        assert flag.evidence_span


def test_unfired_flag_observed_at_falls_back_to_input_max() -> None:
    """Without evidence to anchor it, observed_at falls back to the latest input, never now()."""
    events = [_release_event(T0), _release_event(T0 + timedelta(days=31), "0.2.0")]
    as_of = T0 + timedelta(days=60)
    latest_input = max(event.observed_at for event in events)

    flags = evaluate_events(events, entity_id=ENTITY_ID, as_of=as_of)

    unanchored = [
        flag for flag in flags if not (flag.payload["fired"] and flag.payload["evidence_event_ids"])
    ]
    assert unanchored, "expected at least one unfired/evidence-less flag in this fixture"
    for flag in unanchored:
        assert flag.observed_at == latest_input


# ---------------------------------------------------------------------------
# evaluate(): thin wrapper — signature only, store is still A's stub
# ---------------------------------------------------------------------------


def test_evaluate_wrapper_has_expected_signature() -> None:
    """evaluate() is the store-backed wrapper; verify its shape without touching the store stub."""
    signature = inspect.signature(evaluate)
    assert list(signature.parameters) == ["entity_id", "as_of"]


# ---------------------------------------------------------------------------
# observation(): (y_t, r_t) for A's Kalman filter
# ---------------------------------------------------------------------------


def test_observation_empty_input_returns_uninformative_prior() -> None:
    """No flags observed yet -> feed A's filter the uninformative (0.5, 0.5) prior, not a crash."""
    y_t, r_t = observation([])
    assert (y_t, r_t) == (0.5, 0.5)


def test_observation_y_t_matches_hand_computed_weighted_ratio() -> None:
    """y_t is fired-weight / total-weight, computed straight from the payloads."""
    flags = [
        _flag_event(rule_id="r1", weight=1.0, fired=True),
        _flag_event(rule_id="r2", weight=2.0, fired=False),
        _flag_event(rule_id="r3", weight=3.0, fired=True),
        _flag_event(rule_id="r4", weight=4.0, fired=False),
    ]

    y_t, _ = observation(flags)

    assert y_t == pytest.approx((1.0 + 3.0) / (1.0 + 2.0 + 3.0 + 4.0))
    assert 0.0 <= y_t <= 1.0


def test_observation_r_t_stays_within_bounds_and_clips() -> None:
    """r_t = 0.25/sqrt(n * mean_confidence), clipped to [0.02, 0.5] at both ends."""
    single_low_confidence = [_flag_event(rule_id="r1", weight=1.0, fired=True, confidence=0.01)]
    many_high_confidence = [
        _flag_event(rule_id=f"r{i}", weight=1.0, fired=bool(i % 2), confidence=1.0)
        for i in range(200)
    ]

    _, r_upper_clip = observation(single_low_confidence)
    _, r_lower_clip = observation(many_high_confidence)

    assert r_upper_clip == pytest.approx(0.5)
    assert r_lower_clip == pytest.approx(0.02)
    assert 0.02 <= r_upper_clip <= 0.5
    assert 0.02 <= r_lower_clip <= 0.5


def test_observation_high_weight_fired_rule_moves_y_t_more_than_low_weight() -> None:
    """A high-weight rule firing must move y_t more than a low-weight rule firing."""
    heavy_rule_fires = [
        _flag_event(rule_id="heavy", weight=10.0, fired=True),
        _flag_event(rule_id="light", weight=1.0, fired=False),
    ]
    light_rule_fires = [
        _flag_event(rule_id="heavy", weight=10.0, fired=False),
        _flag_event(rule_id="light", weight=1.0, fired=True),
    ]

    y_heavy_fires, _ = observation(heavy_rule_fires)
    y_light_fires, _ = observation(light_rule_fires)

    assert y_heavy_fires > y_light_fires
