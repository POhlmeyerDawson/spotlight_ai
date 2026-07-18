"""Founder Score. The band must tighten, the trend must be structural, and
nothing may ever see past as_of."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from core.config import Settings
from memory import db, score, store
from schema.events import Event, EventKind, FounderScore, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    yield
    db.reset_connections()


def _flag(entity_id: UUID, day: int, value: float, **kw) -> Event:
    ev = Event(
        kind=kw.pop("kind", EventKind.GREEN_FLAG),
        source=kw.pop("source", Source.WEB),
        entity_id=entity_id,
        observed_at=T0 + timedelta(days=day),
        payload={"value": value, **kw.pop("payload", {})},
        **kw,
    )
    store.append(ev)
    return ev


def _series(entity_id: UUID, values: list[float], step: int = 14) -> None:
    for i, v in enumerate(values):
        _flag(entity_id, i * step, v)


def _at(entity_id: UUID, n_obs: int, step: int = 14) -> datetime:
    return T0 + timedelta(days=(n_obs - 1) * step, hours=1)


# ---------------------------------------------------------------------------


def test_band_tightens_monotonically_as_observations_accumulate() -> None:
    entity_id = uuid4()
    values = [0.7] * 8
    _series(entity_id, values)

    bands = [score.founder(entity_id, _at(entity_id, n)).band for n in range(1, len(values) + 1)]
    assert all(a > b for a, b in zip(bands, bands[1:])), bands
    assert bands[0] < 0.5  # the prior band, already narrowed by the first observation
    assert bands[-1] < bands[0] * 0.7  # and it keeps closing, not just plateauing
    assert bands[-1] < score.P0[0] ** 0.5 / 5  # 5x tighter than knowing nothing


def test_band_widens_again_after_a_long_silence() -> None:
    """Tightening is earned, not permanent — a year of nothing must re-widen."""
    entity_id = uuid4()
    _series(entity_id, [0.7] * 6)
    tight = score.founder(entity_id, _at(entity_id, 6))
    stale = score.founder(entity_id, _at(entity_id, 6) + timedelta(days=365))
    assert stale.band > tight.band
    assert stale.contributing_event_ids == tight.contributing_event_ids


@pytest.mark.parametrize(
    "values,expect",
    [
        ([0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], "positive"),
        ([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3], "negative"),
        ([0.6] * 7, "flat"),
    ],
)
def test_trend_is_structural_momentum(values: list[float], expect: str) -> None:
    entity_id = uuid4()
    _series(entity_id, values)
    got = score.founder(entity_id, _at(entity_id, len(values)))

    if expect == "positive":
        assert got.trend > 1e-3
    elif expect == "negative":
        assert got.trend < -1e-3
    else:
        assert abs(got.trend) < 1e-3

    # nu is a state, not a difference of two mus. A two-point diff would be ~10x larger.
    assert got.trend != pytest.approx(0.0) or expect == "flat"


def test_no_lookahead(tmp_path, monkeypatch) -> None:
    """Events after as_of never touch the score — and scoring the full log at
    t_mid must equal scoring a log truncated at t_mid, cold."""
    entity_id = uuid4()
    values = [0.5, 0.55, 0.6, 0.95, 0.97, 0.99]
    _series(entity_id, values)

    t_mid, t_end = _at(entity_id, 3), _at(entity_id, len(values))
    mid = score.founder(entity_id, t_mid)
    end = score.founder(entity_id, t_end)

    assert mid.mu < end.mu  # the late spike is real, it just must not leak backwards
    assert len(mid.contributing_event_ids) == 3
    assert len(end.contributing_event_ids) == 6

    # Rebuild a log that never contained the future at all.
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "truncated.db"))
    db.reset_connections()
    _series(entity_id, values[:3])
    truncated = score.founder(entity_id, t_mid)

    assert truncated.mu == pytest.approx(mid.mu)
    assert truncated.band == pytest.approx(mid.band)
    assert truncated.trend == pytest.approx(mid.trend)


def test_contradicted_claims_are_excluded_from_observations() -> None:
    entity_id, claim_id = uuid4(), uuid4()
    _series(entity_id, [0.2, 0.2, 0.2])
    tainted = _flag(entity_id, 42, 0.99, payload={"claim_id": str(claim_id)})
    as_of = T0 + timedelta(days=60)

    with_claim = score.founder(entity_id, as_of)
    assert tainted.event_id in with_claim.contributing_event_ids

    store.append(
        Event(
            kind=EventKind.VALIDATION_RESULT,
            source=Source.VALIDATOR,
            company_id=uuid4(),  # validator writes against the company, not the entity
            observed_at=T0 + timedelta(days=50),
            payload={"claim_id": str(claim_id), "status": "contradicted"},
        )
    )
    db.reset_connections()

    after = score.founder(entity_id, as_of)
    assert tainted.event_id not in after.contributing_event_ids
    assert len(after.contributing_event_ids) == 3
    assert after.mu < with_claim.mu
    assert score.observations(entity_id, as_of).dropped_contradicted == [tainted.event_id]


def test_verified_claims_are_kept() -> None:
    entity_id, claim_id = uuid4(), uuid4()
    supported = _flag(entity_id, 0, 0.8, payload={"claim_id": str(claim_id)})
    store.append(
        Event(
            kind=EventKind.VALIDATION_RESULT,
            source=Source.VALIDATOR,
            observed_at=T0,
            payload={"claim_id": str(claim_id), "status": "verified"},
        )
    )
    got = score.founder(entity_id, T0 + timedelta(days=1))
    assert got.contributing_event_ids == [supported.event_id]


def test_score_always_carries_receipts() -> None:
    entity_id = uuid4()
    _series(entity_id, [0.6, 0.7])
    got = score.founder(entity_id, _at(entity_id, 2))
    assert len(got.contributing_event_ids) == 2

    # No observations -> the prior, stated honestly, with no receipts to claim.
    empty = score.founder(uuid4(), T0)
    assert empty.contributing_event_ids == []
    assert empty.mu == pytest.approx(score.MU0)
    assert empty.band == pytest.approx(score.P0[0] ** 0.5)


def test_payload_shapes_are_parsed_defensively() -> None:
    """C writes these in parallel. Accept what we recognise, skip what we don't."""
    entity_id = uuid4()
    _flag(entity_id, 0, 0.6)
    store.append(
        Event(
            kind=EventKind.GREEN_FLAG,
            source=Source.WEB,
            entity_id=entity_id,
            observed_at=T0 + timedelta(days=1),
            payload={"flags": [{"fired": True, "weight": 3.0}, {"fired": False, "weight": 1.0}]},
        )
    )
    store.append(
        Event(  # unfamiliar shape: skipped, not fatal
            kind=EventKind.GREEN_FLAG,
            source=Source.WEB,
            entity_id=entity_id,
            observed_at=T0 + timedelta(days=2),
            payload={"notes": "some prose C decided to write"},
        )
    )
    obs = score.observations(entity_id, T0 + timedelta(days=3))
    assert [o.y for o in obs.kept] == pytest.approx([0.6, 0.75])


def test_proof_events_move_the_score_hard() -> None:
    """The demo moment: low-noise behavioural evidence dominates deck self-report."""
    entity_id = uuid4()
    for i, v in enumerate([0.45, 0.5, 0.48]):
        _flag(entity_id, i * 21, v, source=Source.DECK)
    before = score.founder(entity_id, T0 + timedelta(days=60))

    _flag(entity_id, 70, 0.9, kind=EventKind.PROOF_ARTIFACT, source=Source.PROOF_PROTOCOL)
    _flag(entity_id, 71, 0.85, kind=EventKind.PROOF_BEHAVIOR, source=Source.PROOF_PROTOCOL)
    after = score.founder(entity_id, T0 + timedelta(days=80))

    assert after.mu - before.mu > 0.25
    assert after.band < before.band / 5
    assert after.trend > before.trend


def test_deck_is_noisier_than_proof() -> None:
    deck = score.Observation(uuid4(), T0, 0.9, 0.0)
    ev = Event(kind=EventKind.GREEN_FLAG, source=Source.DECK, observed_at=T0)
    proof = Event(kind=EventKind.PROOF_ARTIFACT, source=Source.PROOF_PROTOCOL, observed_at=T0)
    assert score._noise(ev, {}) > score._noise(proof, {})
    assert deck.y == 0.9  # dataclass sanity


def test_low_self_consistency_widens_noise() -> None:
    ev = Event(kind=EventKind.GREEN_FLAG, source=Source.WEB, observed_at=T0)
    assert score._noise(ev, {"self_consistency": 0.2}) > score._noise(ev, {"self_consistency": 1.0})
    assert score._noise(ev, {"self_consistency": 0.0}) < float("inf")  # no zero-divide


def test_forecast_propagates_uncertainty_forward() -> None:
    entity_id = uuid4()
    _series(entity_id, [0.3, 0.4, 0.5, 0.6, 0.7])
    as_of = _at(entity_id, 5)
    now = score.founder(entity_id, as_of)

    mu_30, band_30 = score.forecast(entity_id, as_of, 30)
    mu_90, band_90 = score.forecast(entity_id, as_of, 90)

    assert mu_30 > now.mu  # rising founder keeps rising
    assert mu_90 >= mu_30
    assert band_90 > band_30 > now.band  # the further out, the less we know
    assert 0.0 <= mu_90 <= 1.0


# ---------------------------------------------------------------------------
# The fallback flag. Verified at H10, not H20.
# ---------------------------------------------------------------------------


def _use_model(monkeypatch, name: str) -> None:
    monkeypatch.setenv("SCORE_MODEL", name)
    monkeypatch.setattr("core.config.settings", Settings())


def test_score_model_flag_dispatches_to_the_fallback(monkeypatch) -> None:
    entity_id = uuid4()
    _series(entity_id, [0.4, 0.6, 0.8, 0.9])
    as_of = _at(entity_id, 4)

    assert score.founder(entity_id, as_of).model == "kalman"

    _use_model(monkeypatch, "beta_binomial")
    got = score.founder(entity_id, as_of)
    assert isinstance(got, FounderScore)
    assert got.model == "beta_binomial"
    assert got.entity_id == entity_id and got.as_of == as_of
    assert 0.0 < got.mu < 1.0
    assert got.band > 0.0
    assert got.trend > 0.0  # same rising series, same sign
    assert len(got.contributing_event_ids) == 4


def test_fallback_honours_contradictions_and_the_empty_case(monkeypatch) -> None:
    _use_model(monkeypatch, "beta_binomial")
    entity_id, claim_id = uuid4(), uuid4()
    _series(entity_id, [0.2, 0.2])
    tainted = _flag(entity_id, 30, 0.99, payload={"claim_id": str(claim_id)})
    store.append(
        Event(
            kind=EventKind.VALIDATION_RESULT,
            source=Source.VALIDATOR,
            observed_at=T0,
            payload={"claims": [{"claim_id": str(claim_id), "status": "contradicted"}]},
        )
    )
    got = score.founder(entity_id, T0 + timedelta(days=40))
    assert tainted.event_id not in got.contributing_event_ids
    assert got.mu < 0.5

    empty = score.founder(uuid4(), T0)
    assert empty.model == "beta_binomial"
    assert empty.mu == pytest.approx(0.5)
    assert empty.contributing_event_ids == []
