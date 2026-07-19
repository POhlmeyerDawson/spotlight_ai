"""The request-scoped scoring cache must be invisible in every way except speed.

`memory/score.py` grew a `scoring_cache()` block because `GET /companies` was issuing
~5.4 database round trips per company — running the Kalman filter twice per founder and
re-scanning corpus-wide event kinds once per founder. Profiling put 7.0s of a 7.8s
request inside psycopg's wait.

A cache that changes an answer is worse than the latency it fixes, so the property
pinned here is EQUIVALENCE, not hit rate: for the same store and the same `as_of`,
scores computed inside a cache block and outside one must be identical, and a cache
must never outlive the block that opened it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from memory import score as score_mod, store
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=400)


def _flag(entity_id, day: int, value: float, rule: str = "ships_regularly") -> Event:
    """A green-flag ROLLUP, which is what the scorer actually observes.

    Deliberately not a per-rule receipt: `_derive_y` returns None for a payload that
    only carries `rule_id`/`fired`, because a receipt records which rule ran, and the
    reading is the rollup. A fixture built the other way scores every entity at the
    prior and makes any assertion about the cache vacuous.
    """
    return Event(
        kind=EventKind.GREEN_FLAG,
        source=Source.GITHUB,
        source_url="https://github.com/someone/thing",
        observed_at=T0 + timedelta(days=day),
        entity_id=entity_id,
        payload={"value": value, "rule_ids": [rule], "evidence_event_ids": []},
    )


@pytest.fixture
def populated(monkeypatch):
    store.reset()
    entities = []
    for n in range(4):
        eid = store.upsert_entity(f"Person {n}", f"person {n}")
        entities.append(eid)
        for day in range(0, 60, 10):
            store.append(_flag(eid, day, value=0.3 + 0.1 * ((n + day // 10) % 5)))
    return entities


def _shape(fs) -> tuple:
    return (
        round(fs.mu, 12),
        round(fs.band, 12),
        round(fs.trend, 12),
        tuple(sorted(str(i) for i in fs.contributing_event_ids)),
    )


def test_cached_and_uncached_scores_are_identical(populated) -> None:
    """The whole justification for the cache. If this drifts, the cache is a bug."""
    plain = {e: _shape(score_mod.founder(e, AS_OF)) for e in populated}
    with score_mod.scoring_cache():
        cached = {e: _shape(score_mod.founder(e, AS_OF)) for e in populated}
    assert plain == cached


def test_the_cache_does_not_leak_past_its_block(populated) -> None:
    """Appending evidence and rescoring at the same as_of must reflect the append.

    A module-level lru_cache keyed on (entity, as_of) would silently return the
    pre-append score here — which is exactly why the cache is scoped to a block the
    caller opens, rather than always on.
    """
    entity = populated[0]
    with score_mod.scoring_cache():
        before = score_mod.founder(entity, AS_OF).mu

    for day in range(60, 200, 10):
        store.append(_flag(entity, day, value=0.95, rule="sustained_delivery"))

    after = score_mod.founder(entity, AS_OF).mu
    assert after != before, "a score computed after the block ignored the new evidence"


def test_a_stale_read_inside_a_block_is_the_documented_tradeoff(populated) -> None:
    """Inside a block the caller ASSERTS the store is fixed. Pinned so the contract is
    explicit rather than folklore — a future caller wrapping a WRITE path in this
    block would be relying on behaviour this test says it does not have."""
    entity = populated[0]
    with score_mod.scoring_cache():
        first = score_mod.founder(entity, AS_OF).mu
        for day in range(60, 200, 10):
            store.append(_flag(entity, day, value=0.95, rule="sustained_delivery"))
        second = score_mod.founder(entity, AS_OF).mu
    assert first == second


def test_nested_blocks_share_one_cache_rather_than_shadowing(populated) -> None:
    entity = populated[0]
    with score_mod.scoring_cache():
        outer = score_mod.founder(entity, AS_OF).mu
        with score_mod.scoring_cache():
            inner = score_mod.founder(entity, AS_OF).mu
        # Still cached after the inner block exits — the inner one must not have
        # reset the outer cache on its way out.
        assert score_mod._CACHE.get() is not None
    assert outer == inner
    assert score_mod._CACHE.get() is None


def test_the_index_returns_only_scorable_kinds(populated) -> None:
    """The corpus-wide index replaced a per-entity fetch that filtered to
    _OBSERVATION_KINDS in Python. It must apply the same filter."""
    entity = populated[0]
    store.append(
        Event(
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            source_url="https://github.com/someone/thing",
            observed_at=T0 + timedelta(days=5),
            entity_id=entity,
            payload={"text": "a push that is not a derived observation"},
        )
    )
    with score_mod.scoring_cache():
        events = score_mod._observation_events_for(entity, AS_OF)
    assert events, "the index dropped everything"
    assert all(e.kind in score_mod._OBSERVATION_KINDS for e in events)


def test_entities_with_no_evidence_are_absent_from_the_index_not_zeroed(populated) -> None:
    """Absence must stay absence. An entity the index has never seen returns an empty
    list, which build_observations turns into the uninformative prior — not a
    confident zero."""
    unknown = uuid4()
    with score_mod.scoring_cache():
        assert score_mod._observation_events_for(unknown, AS_OF) == []
        fs = score_mod.founder(unknown, AS_OF)
    assert fs.mu == pytest.approx(0.5)
    assert fs.band >= 0.4, "no evidence must widen the band, not narrow it"


# --- a diverged filter must never present as certainty ---------------------------


def test_a_diverged_filter_returns_the_prior_not_a_confident_zero(monkeypatch) -> None:
    """The failure real sourcing exposed, pinned.

    `_run_filter` uses the textbook `P = (I - K H) P` update, which can lose positive
    definiteness after a long propagation — and real GitHub profiles supply one, because
    the scanner stamps them with the ACCOUNT CREATION date, putting 13 years between a
    founder's first and second observation. 8 of 130 sourced founders diverged.

    The reporting is what made it dangerous: `sqrt(max(var, 0.0))` turned a NEGATIVE
    variance into band=0.0, and clipping turned a state of -23.6 into mu=0.0. A founder
    whose observations averaged 0.36 was published as a confident zero. Absence of a
    usable answer must read as absence, never as certainty.
    """
    import numpy as np

    entity = uuid4()
    monkeypatch.setattr(
        score_mod,
        "_run_filter",
        lambda eid, as_of: (
            np.array([-23.55, -113.71]),
            np.array([[-3.60, -17.21], [-17.21, -81.64]]),
            [],
        ),
    )
    fs = score_mod.founder(entity, AS_OF)
    assert fs.mu == pytest.approx(0.5), "a diverged filter must not report a low score"
    assert fs.band >= 0.4, "a diverged filter must report a WIDE band, not a certain one"


def test_a_healthy_posterior_is_returned_untouched(monkeypatch) -> None:
    """The guard must not re-tune anything that was already valid."""
    import numpy as np

    entity = uuid4()
    monkeypatch.setattr(
        score_mod,
        "_run_filter",
        lambda eid, as_of: (np.array([0.73, 0.04]), np.array([[0.0225, 0.0], [0.0, 0.01]]), []),
    )
    fs = score_mod.founder(entity, AS_OF)
    assert fs.mu == pytest.approx(0.73)
    assert fs.band == pytest.approx(0.15)
    assert fs.trend == pytest.approx(0.04)
