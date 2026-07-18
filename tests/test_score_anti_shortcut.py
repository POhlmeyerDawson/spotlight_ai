"""Focused anti-shortcut checks against the real Founder Score path.

These are component invariants over controlled synthetic histories, not evidence
of real-world predictive validity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory import score, store
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _entity(name: str):
    return store.get_store().create_entity(name, name.casefold()).entity_id


def _append(entity_id, observed_at, *, kind, source, value=0.7, self_consistency=0.9):
    return store.append(
        Event(
            entity_id=entity_id,
            kind=kind,
            source=source,
            observed_at=observed_at,
            payload={"value": value, "self_consistency": self_consistency},
        )
    )


def test_raw_event_volume_does_not_change_score_or_receipts() -> None:
    quiet = _entity("Quiet builder")
    famous = _entity("Famous builder")
    _append(
        quiet,
        T0,
        kind=EventKind.PROOF_ARTIFACT,
        source=Source.PROOF_PROTOCOL,
        value=0.85,
        self_consistency=0.95,
    )
    _append(
        famous,
        T0,
        kind=EventKind.PROOF_ARTIFACT,
        source=Source.PROOF_PROTOCOL,
        value=0.85,
        self_consistency=0.95,
    )
    for index in range(100):
        _append(
            famous,
            T0 + timedelta(minutes=index),
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            value=1.0,
        )
        _append(
            famous,
            T0 + timedelta(minutes=index),
            kind=EventKind.HN_POST,
            source=Source.HN,
            value=1.0,
        )

    quiet_score = score.founder(quiet, T0 + timedelta(days=1))
    famous_score = score.founder(famous, T0 + timedelta(days=1))
    assert famous_score.mu == quiet_score.mu
    assert famous_score.band == quiet_score.band
    assert len(famous_score.contributing_event_ids) == len(quiet_score.contributing_event_ids) == 1


def test_repeated_same_signal_is_bounded_and_diverse_evidence_wins() -> None:
    duplicate = _entity("Duplicate evidence")
    diverse = _entity("Independent evidence")
    for _ in range(50):
        _append(duplicate, T0, kind=EventKind.GREEN_FLAG, source=Source.MANUAL, value=0.72)
    for index, value in enumerate((0.6, 0.75, 0.9)):
        _append(
            diverse,
            T0 + timedelta(days=30 * index),
            kind=EventKind.GREEN_FLAG,
            source=Source.MANUAL,
            value=value,
        )

    duplicate_score = score.founder(duplicate, T0 + timedelta(days=90))
    diverse_score = score.founder(diverse, T0 + timedelta(days=90))
    assert duplicate_score.mu <= 0.72 + 1e-9
    assert diverse_score.mu > duplicate_score.mu


def test_low_quality_high_volume_does_not_beat_one_strong_proof() -> None:
    control = _entity("High-volume control")
    strong = _entity("Lower-volume strong")
    for index in range(50):
        _append(
            control,
            T0 + timedelta(days=index),
            kind=EventKind.GREEN_FLAG,
            source=Source.WEB,
            value=0.55,
            self_consistency=0.3,
        )
    _append(
        strong,
        T0,
        kind=EventKind.PROOF_ARTIFACT,
        source=Source.PROOF_PROTOCOL,
        value=0.85,
        self_consistency=0.95,
    )

    assert (
        score.founder(strong, T0 + timedelta(days=60)).mu
        > score.founder(control, T0 + timedelta(days=60)).mu
    )


def test_display_name_does_not_influence_score() -> None:
    ordinary = _entity("Ordinary builder")
    fame_like = _entity("World Famous Billionaire Celebrity")
    for entity_id in (ordinary, fame_like):
        _append(
            entity_id,
            T0,
            kind=EventKind.PROOF_BEHAVIOR,
            source=Source.VALIDATOR,
            value=0.8,
            self_consistency=0.9,
        )

    ordinary_score = score.founder(ordinary, T0 + timedelta(days=1))
    fame_like_score = score.founder(fame_like, T0 + timedelta(days=1))
    assert (ordinary_score.mu, ordinary_score.band, ordinary_score.trend) == (
        fame_like_score.mu,
        fame_like_score.band,
        fame_like_score.trend,
    )
