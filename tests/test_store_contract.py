"""Backend contract: the in-memory and Postgres stores must be externally
indistinguishable. Every test below runs against the in-memory backend always, and
against Postgres too when MEMORY_PG_TEST_URL points at a throwaway test database
(the CI/offline suite never needs it). If the two backends ever diverge, one of
these fails.

The selector tests at the bottom prove MEMORY_BACKEND wiring and clear failures on
misconfiguration, and need no database.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from memory import store
from memory.store import EventStore
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_PG_URL = os.getenv("MEMORY_PG_TEST_URL")

_BACKENDS = ["memory"] + (["postgres"] if _PG_URL else [])


@pytest.fixture(params=_BACKENDS)
def backend(request):
    """A fresh store of each supported backend. Postgres is truncated before and
    after — MEMORY_PG_TEST_URL must be a disposable database, never a real one."""
    if request.param == "memory":
        yield EventStore()
        return
    from memory.pg_store import PostgresEventStore

    pg = PostgresEventStore(_PG_URL)
    pg.reset()
    try:
        yield pg
    finally:
        pg.reset()
        pg.close()


def _evt(**kw) -> Event:
    base = dict(kind=EventKind.REPO_ACTIVITY, source=Source.GITHUB, observed_at=T0)
    base.update(kw)
    return Event(**base)


# -- events -----------------------------------------------------------------


def test_append_then_read_back(backend) -> None:
    eid = backend.append(_evt())
    assert [e.event_id for e in backend.events(as_of=T0)] == [eid]


def test_empty_history_returns_empty(backend) -> None:
    assert backend.events(as_of=T0) == []


def test_as_of_cutoff_excludes_future(backend) -> None:
    ent = backend.create_entity("A", "a").entity_id
    e1 = backend.append(_evt(entity_id=ent, observed_at=T0))
    e2 = backend.append(_evt(entity_id=ent, observed_at=T0 + timedelta(days=30)))
    e3 = backend.append(_evt(entity_id=ent, observed_at=T0 + timedelta(days=60)))
    ids = {e.event_id for e in backend.events(as_of=T0 + timedelta(days=30), entity_id=ent)}
    assert e1 in ids and e2 in ids
    assert e3 not in ids


def test_filters_entity_company_kind(backend) -> None:
    a = backend.create_entity("A", "a").entity_id
    b = backend.create_entity("B", "b").entity_id
    co = backend.create_company("Co").company_id
    backend.append(_evt(entity_id=a, company_id=co, kind=EventKind.RELEASE))
    backend.append(_evt(entity_id=b, kind=EventKind.HN_POST, source=Source.HN))
    assert len(backend.events(as_of=T0, entity_id=a)) == 1
    assert len(backend.events(as_of=T0, company_id=co)) == 1
    assert len(backend.events(as_of=T0, kind=EventKind.HN_POST)) == 1
    assert len(backend.events(as_of=T0, kind="release")) == 1  # str and enum both work
    assert backend.events(as_of=T0, entity_id=uuid4()) == []
    assert backend.events(as_of=T0, company_id=uuid4()) == []
    assert backend.events(as_of=T0, kind="missing") == []


def test_deterministic_order_by_observed_at(backend) -> None:
    backend.append(_evt(observed_at=T0 + timedelta(days=5)))
    backend.append(_evt(observed_at=T0 + timedelta(days=1)))
    backend.append(_evt(observed_at=T0 + timedelta(days=3)))
    times = [e.observed_at for e in backend.events(as_of=T0 + timedelta(days=10))]
    assert times == sorted(times)


def test_deterministic_order_for_equal_event_timestamps(backend) -> None:
    first = _evt(event_id=UUID(int=2), ingested_at=T0)
    second = _evt(event_id=UUID(int=1), ingested_at=T0)
    backend.append(first)
    backend.append(second)
    assert [event.event_id for event in backend.events(as_of=T0)] == [
        second.event_id,
        first.event_id,
    ]


def test_correction_is_a_new_event(backend) -> None:
    ent = backend.create_entity("N", "n").entity_id
    backend.append(
        _evt(
            entity_id=ent,
            kind=EventKind.PROFILE_FACT,
            observed_at=T0,
            payload={"key": "role", "value": "eng"},
        )
    )
    backend.append(
        _evt(
            entity_id=ent,
            kind=EventKind.PROFILE_FACT,
            observed_at=T0 + timedelta(days=1),
            payload={"key": "role", "value": "cto"},
        )
    )
    assert len(backend.events(as_of=T0 + timedelta(days=2), entity_id=ent)) == 2


def test_no_event_mutation_surface(backend) -> None:
    assert not hasattr(backend, "update_event")
    assert not hasattr(backend, "delete_event")


def test_provenance_round_trips(backend) -> None:
    backend.append(
        _evt(
            source_url="https://github.com/x/y",
            evidence_span="commit deadbeef",
            integrity_flags=["ocr_low_conf", "transliterated_name"],
            confidence=0.42,
            payload={"nested": {"n": 1}, "list": [1, 2, 3]},
        )
    )
    e = backend.events(as_of=T0)[0]
    assert e.source_url == "https://github.com/x/y"
    assert e.evidence_span == "commit deadbeef"
    assert e.integrity_flags == ["ocr_low_conf", "transliterated_name"]
    assert e.confidence == pytest.approx(0.42)
    assert e.payload == {"nested": {"n": 1}, "list": [1, 2, 3]}


def test_naive_as_of_rejected(backend) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        backend.events(as_of=datetime(2024, 1, 1))  # noqa: DTZ001 — the point of the test


# -- entities / aliases / companies / merges --------------------------------


def test_entity_persistence(backend) -> None:
    ent = backend.create_entity("Ada Okafor", "ada okafor")
    got = backend.get_entity(ent.entity_id)
    assert got is not None and got.display_name == "Ada Okafor"
    assert ent.entity_id in {e.entity_id for e in backend.entities()}
    assert backend.get_entity(uuid4()) is None


def test_empty_registry_reads_are_empty(backend) -> None:
    assert backend.entities() == []
    assert backend.companies() == []
    assert backend.aliases_for(uuid4()) == []
    assert backend.aliases_by_kind("email") == []
    assert backend.merges() == []


def test_alias_first_writer_wins(backend) -> None:
    a = backend.create_entity("A", "a").entity_id
    b = backend.create_entity("B", "b").entity_id
    assert backend.add_alias(a, "email", "x@y.z", "m") == a
    assert backend.find_by_alias("email", "x@y.z") == a
    # b tries to claim the same identifier: it does NOT get reassigned.
    assert backend.add_alias(b, "email", "x@y.z", "m") == a
    assert backend.find_by_alias("email", "x@y.z") == a
    assert backend.find_by_alias("email", "missing") is None


def test_aliases_for_and_by_kind(backend) -> None:
    a = backend.create_entity("A", "a").entity_id
    backend.add_alias(a, "email", "a@b.c", "m")
    backend.add_alias(a, "handle:github", "aaa", "github")
    assert {al.kind for al in backend.aliases_for(a)} == {"email", "handle:github"}
    assert [al.value for al in backend.aliases_by_kind("email")] == ["a@b.c"]


def test_company_persistence_including_empty_and_full_founders(backend) -> None:
    empty = backend.create_company("EmptyCo")
    assert backend.get_company(empty.company_id).founder_entity_ids == []
    f1, f2 = uuid4(), uuid4()
    full = backend.create_company("FullCo", founder_entity_ids=[f1, f2], archetype=3)
    got = backend.get_company(full.company_id)
    assert got.founder_entity_ids == [f1, f2]
    assert got.archetype == 3
    assert {c.company_id for c in backend.companies()} == {empty.company_id, full.company_id}


def test_merge_persistence_and_status_filter(backend) -> None:
    # Real entities: Postgres enforces the merges->entities foreign keys, and the
    # resolver only ever records merges between entities that exist.
    a = backend.create_entity("A", "a").entity_id
    b = backend.create_entity("B", "b").entity_id
    c = backend.create_entity("C", "c").entity_id
    backend.record_merge(a, b, "ambiguous", 0.5, "unsure")
    backend.record_merge(a, c, "merged", 0.9, "sure")
    assert len(backend.merges()) == 2
    ambiguous = backend.merges(status="ambiguous")
    assert len(ambiguous) == 1 and ambiguous[0].entity_b == b


# -- selector (no database) -------------------------------------------------


def test_selector_defaults_to_memory_when_no_database_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    monkeypatch.setattr("core.config.settings", SimpleNamespace(database_url=""))
    assert store.get_store() is store._default


def test_selector_infers_postgres_from_database_url(monkeypatch) -> None:
    """With no explicit MEMORY_BACKEND, a configured Postgres URL must select the
    Postgres backend. Defaulting to in-memory here reads an empty ephemeral store
    while real rows sit in the database, and reports no error."""
    from memory.pg_store import PostgresEventStore

    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    monkeypatch.setattr(
        "core.config.settings", SimpleNamespace(database_url="postgresql://x/y")
    )
    store._pg = None
    assert isinstance(store.get_store(), PostgresEventStore)


def test_selector_postgres_returns_pg_backend(monkeypatch) -> None:
    from memory.pg_store import PostgresEventStore

    monkeypatch.setenv("MEMORY_BACKEND", "postgres")
    monkeypatch.setattr("core.config.settings", SimpleNamespace(database_url="postgresql://x/y"))
    store._pg = None
    assert isinstance(store.get_store(), PostgresEventStore)  # lazy — no connection made


def test_selector_postgres_without_url_raises(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_BACKEND", "postgres")
    monkeypatch.setattr("core.config.settings", SimpleNamespace(database_url=""))
    store._pg = None
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        store.get_store()


def test_selector_unknown_backend_raises(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="unknown MEMORY_BACKEND"):
        store.get_store()


# -- module-level compat helpers re-exported by memory/__init__ -------------
#
# These are part of A's public contract but had no coverage, which is how a
# NameError in clear()/count() survived. Exercised against the in-memory default.


def test_clear_count_and_get_event_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_BACKEND", "memory")
    store.clear()
    assert store.count() == 0

    first = _evt()
    second = _evt(observed_at=T0 + timedelta(days=1))
    store.append(first)
    store.append(second)

    assert store.count() == 2
    assert store.get_event(first.event_id) == first
    # get_event is unscoped by as_of: a future event is still retrievable by id.
    assert store.get_event(second.event_id) == second
    assert store.get_event(uuid4()) is None

    store.clear()
    assert store.count() == 0
    assert store.get_event(first.event_id) is None


def test_clear_never_truncates_a_real_database(monkeypatch) -> None:
    """clear() must route through reset(), which refuses to touch Postgres."""
    monkeypatch.setenv("MEMORY_BACKEND", "postgres")
    monkeypatch.setattr(
        store, "_get_pg_store", lambda: pytest.fail("clear() must not reach the pg backend")
    )
    store.clear()


# -- Postgres-only: DB-level append-only (needs a real database) ------------


@pytest.mark.skipif(not _PG_URL, reason="MEMORY_PG_TEST_URL not set")
def test_postgres_enforces_append_only_at_db_level() -> None:
    import psycopg

    from memory.pg_store import PostgresEventStore

    pg = PostgresEventStore(_PG_URL)
    pg.reset()
    try:
        eid = pg.append(_evt())
        conn = psycopg.connect(_PG_URL, autocommit=True)
        try:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute("update events set confidence = 0.1 where event_id = %s", (eid,))
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute("delete from events where event_id = %s", (eid,))
        finally:
            conn.close()
    finally:
        pg.reset()
        pg.close()
