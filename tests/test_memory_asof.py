"""The insurance policy. If events() ever returns the future, this fails loudly."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from memory import db, queries, store
from schema.events import Event, EventKind, Source

T1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=30)
T3 = T1 + timedelta(days=60)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    """Never touch the real db."""
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    yield
    db.reset_connections()


def _event(observed_at: datetime, **kw) -> Event:
    return Event(
        kind=kw.pop("kind", EventKind.REPO_ACTIVITY),
        source=kw.pop("source", Source.GITHUB),
        observed_at=observed_at,
        **kw,
    )


def test_as_of_never_returns_the_future() -> None:
    entity_id = uuid4()
    for t in (T1, T2, T3):
        store.append(_event(t, entity_id=entity_id))

    got = store.events(as_of=T2, entity_id=entity_id)
    assert [e.observed_at for e in got] == [T1, T2]
    assert all(e.observed_at <= T2 for e in got)


def test_events_are_ordered_by_observed_at() -> None:
    entity_id = uuid4()
    for t in (T3, T1, T2):  # inserted out of order
        store.append(_event(t, entity_id=entity_id))
    got = store.events(as_of=T3, entity_id=entity_id)
    assert [e.observed_at for e in got] == [T1, T2, T3]


def test_boundary_is_inclusive() -> None:
    entity_id = uuid4()
    store.append(_event(T2, entity_id=entity_id))
    assert len(store.events(as_of=T2, entity_id=entity_id)) == 1
    assert store.events(as_of=T2 - timedelta(microseconds=1), entity_id=entity_id) == []


def test_datetimes_round_trip_tz_aware() -> None:
    entity_id = uuid4()
    observed = datetime(2024, 1, 4, 5, 6, 7, 891234, tzinfo=timezone.utc)
    original = _event(observed, entity_id=entity_id, payload={"repo": "x/y"}, confidence=0.4)
    store.append(original)

    got = store.events(as_of=T3, entity_id=entity_id)[0]
    assert got.observed_at.tzinfo is not None
    assert got.observed_at == observed
    assert got.ingested_at.tzinfo is not None
    assert got.ingested_at == original.ingested_at
    assert got.payload == {"repo": "x/y"}
    assert got.confidence == pytest.approx(0.4)


def test_non_utc_input_normalizes() -> None:
    entity_id = uuid4()
    ist = timezone(timedelta(hours=5, minutes=30))
    observed = datetime(2024, 2, 1, 12, 0, tzinfo=ist)
    store.append(_event(observed, entity_id=entity_id))
    got = store.events(as_of=T3, entity_id=entity_id)[0]
    assert got.observed_at == observed  # same instant, stored as UTC


def test_entity_company_and_kind_filters() -> None:
    e1, e2, c1 = uuid4(), uuid4(), uuid4()
    store.append(_event(T1, entity_id=e1, company_id=c1))
    store.append(_event(T1, entity_id=e2))
    store.append(_event(T1, company_id=c1, kind=EventKind.DECK_CLAIM, source=Source.DECK))

    assert len(store.events(as_of=T3, entity_id=e1)) == 1
    assert len(store.events(as_of=T3, company_id=c1)) == 2
    assert len(store.events(as_of=T3, company_id=c1, kind=EventKind.DECK_CLAIM)) == 1
    assert len(store.events(as_of=T3)) == 3


def test_append_only_rejects_update_and_delete() -> None:
    event_id = store.append(_event(T1, entity_id=uuid4()))
    conn = db.connect()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("update events set confidence = 0.1 where event_id = ?", (str(event_id),))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("delete from events where event_id = ?", (str(event_id),))
    assert len(store.events(as_of=T3)) == 1


def test_upsert_entity_and_company_are_idempotent() -> None:
    a = store.upsert_entity("Ólafur Þórðarson", "olafur thordarson")
    b = store.upsert_entity("Olafur Thordarson", "olafur thordarson")
    assert a == b
    assert store.get_entity(a)["display_name"] == "Ólafur Þórðarson"

    c1 = store.upsert_company("Acme", archetype=2)
    assert c1 == store.upsert_company("Acme")
    assert store.get_company(c1)["archetype"] == 2

    assert len(store.all_entities()) == 1
    assert len(store.all_companies()) == 1
    assert store.get_entity(uuid4()) is None
    assert store.get_company(uuid4()) is None


def test_queries_are_as_of_scoped() -> None:
    entity_id, company_id = uuid4(), uuid4()
    store.append(_event(T1, entity_id=entity_id))
    store.append(_event(T3, entity_id=entity_id))
    store.append(_event(T1, company_id=company_id, kind=EventKind.DECK_CLAIM, source=Source.DECK))
    store.append(_event(T3, company_id=company_id, kind=EventKind.DECK_CLAIM, source=Source.DECK))
    store.append(_event(T1, company_id=company_id, kind=EventKind.HN_POST, source=Source.HN))

    assert len(queries.timeline(entity_id, as_of=T2)) == 1
    got = queries.claims(company_id, as_of=T2)
    assert len(got) == 1
    assert got[0].kind == EventKind.DECK_CLAIM
