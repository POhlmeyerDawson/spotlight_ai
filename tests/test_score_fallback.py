"""Beta-Binomial fallback: works standalone, satisfies the FounderScore contract,
and the SCORE_MODEL flag actually switches the model (verified now, not at H20)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory import score, score_fallback, store
from schema.events import Event, EventKind, FounderScore, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _entity_with_flags():
    eid = store.get_store().create_entity("F", "f").entity_id
    for month, value in [(0, 0.6), (3, 0.75), (6, 0.85)]:
        store.append(
            Event(
                entity_id=eid,
                kind=EventKind.GREEN_FLAG,
                source=Source.MANUAL,
                observed_at=T0 + timedelta(days=30 * month),
                payload={"value": value, "self_consistency": 0.9},
            )
        )
    return eid


def test_fallback_returns_valid_founderscore_independently() -> None:
    eid = _entity_with_flags()
    fs = score_fallback.founder(eid, as_of=T0 + timedelta(days=200))
    assert isinstance(fs, FounderScore)
    assert fs.model == "beta_binomial"
    assert 0.0 <= fs.mu <= 1.0
    assert fs.band >= 0.0
    assert len(fs.contributing_event_ids) == 3


def test_fallback_lifts_mu_on_positive_evidence() -> None:
    eid = _entity_with_flags()
    fs = score_fallback.founder(eid, as_of=T0 + timedelta(days=200))
    assert fs.mu > 0.5


def test_flag_switches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    eid = _entity_with_flags()
    as_of = T0 + timedelta(days=200)

    assert score.founder(eid, as_of=as_of).model == "kalman"  # default

    monkeypatch.setenv("SCORE_MODEL", "beta_binomial")
    switched = score.founder(eid, as_of=as_of)
    assert switched.model == "beta_binomial"
    assert switched.contributing_event_ids  # receipts survive the switch


def test_fallback_no_evidence_is_neutral() -> None:
    eid = store.get_store().create_entity("Empty", "empty").entity_id
    fs = score_fallback.founder(eid, as_of=T0)
    assert abs(fs.mu - 0.5) < 1e-9
    assert fs.contributing_event_ids == []
