"""Persistent Postgres backend for Memory. Owner: A.

Same public contract as the in-memory EventStore (memory/store.py): append(),
as_of-scoped events(), and the entity / alias / company / merge registry. Nothing
downstream (resolver, score, queries, B/C/D) knows or cares which backend is
active — memory/store.get_store() picks one from MEMORY_BACKEND.

Mirrors schema/migrations/001_init.sql exactly. Append-only is enforced by the
database (the events UPDATE/DELETE trigger), not re-implemented here — a rejected
UPDATE surfaces as a psycopg error, exactly as it should. The observed_at <=
as_of cutoff is applied IN the SQL, never by fetching everything and filtering in
Python. Every statement is parameterized; no value is ever interpolated into SQL.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory.store import Alias, Merge
from schema.events import Company, Entity, Event

_EVENT_COLS = (
    "event_id, entity_id, company_id, kind, source, source_url, "
    "observed_at, ingested_at, payload, evidence_span, confidence, integrity_flags"
)
_ENTITY_COLS = "entity_id, display_name, name_normalized, created_at"
_COMPANY_COLS = "company_id, name, founder_entity_ids, archetype, created_at"
_ALIAS_COLS = "entity_id, kind, value, source"
_MERGE_COLS = "entity_a, entity_b, status, score, rationale, decided_at"


def _coerce(row: dict) -> dict:
    """Normalise a stored row before validation.

    `integrity_flags` is text[] in the schema, but a database that has lived through
    several migration runs holds rows written against a jsonb version of the column,
    where an empty list landed as the empty OBJECT {} rather than an array. Because
    Event.integrity_flags is a list, ONE such row raised a ValidationError that
    aborted the entire query — the store returned nothing while the database held
    every event. A single malformed value must never cost the whole read.
    """
    flags = row.get("integrity_flags")
    if not isinstance(flags, list):
        row["integrity_flags"] = []
    payload = row.get("payload")
    if not isinstance(payload, dict):
        row["payload"] = {}
    return row


class PostgresEventStore:
    """Persistent event store, duck-compatible with memory.store.EventStore."""

    def __init__(self, conninfo: str) -> None:
        if not conninfo:
            raise ValueError("PostgresEventStore requires a non-empty connection string")
        self._conninfo = conninfo
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        # autocommit: the log is append-only and every write is one statement, so
        # there is no multi-statement transaction to coordinate.
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._conninfo, autocommit=True)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    # -- events -------------------------------------------------------------

    def append(self, event: Event) -> UUID:
        with self._connection().cursor() as cur:
            cur.execute(
                f"insert into events ({_EVENT_COLS}) values "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "on conflict (event_id) do nothing",
                (
                    event.event_id,
                    event.entity_id,
                    event.company_id,
                    event.kind.value,
                    event.source.value,
                    event.source_url,
                    event.observed_at,
                    event.ingested_at,
                    Jsonb(event.payload),
                    event.evidence_span,
                    event.confidence,
                    # text[] per schema/migrations/001_init.sql — psycopg adapts a
                    # python list directly. Wrapping it in Jsonb() targets a jsonb
                    # column that only ever existed from a superseded migration run.
                    list(event.integrity_flags),
                ),
            )
        return event.event_id

    def events(
        self,
        *,
        as_of: datetime,
        entity_id: UUID | None = None,
        company_id: UUID | None = None,
        kind: str | None = None,
    ) -> list[Event]:
        """Returns only events with observed_at <= as_of. The cutoff is in the
        WHERE clause; the (entity_id, observed_at) / (company_id, observed_at)
        indexes serve it. Deterministic order matches the in-memory backend."""
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware — a naive as_of silently mis-filters")
        clauses = ["observed_at <= %s"]
        params: list = [as_of]
        if entity_id is not None:
            clauses.append("entity_id = %s")
            params.append(entity_id)
        if company_id is not None:
            clauses.append("company_id = %s")
            params.append(company_id)
        if kind is not None:
            clauses.append("kind = %s")
            params.append(str(kind))
        sql = (
            f"select {_EVENT_COLS} from events where "
            + " and ".join(clauses)
            + " order by observed_at, ingested_at, event_id"
        )
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [Event(**_coerce(row)) for row in cur.fetchall()]

    # -- entities -----------------------------------------------------------

    def create_entity(self, display_name: str, name_normalized: str) -> Entity:
        entity = Entity(display_name=display_name, name_normalized=name_normalized)
        with self._connection().cursor() as cur:
            cur.execute(
                f"insert into entities ({_ENTITY_COLS}) values (%s,%s,%s,%s)",
                (entity.entity_id, entity.display_name, entity.name_normalized, entity.created_at),
            )
        return entity

    def get_entity(self, entity_id: UUID) -> Entity | None:
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(f"select {_ENTITY_COLS} from entities where entity_id = %s", (entity_id,))
            row = cur.fetchone()
        return Entity(**row) if row else None

    def entities(self) -> list[Entity]:
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(f"select {_ENTITY_COLS} from entities order by entity_id")
            return [Entity(**row) for row in cur.fetchall()]

    # -- aliases ------------------------------------------------------------

    def add_alias(self, entity_id: UUID, kind: str, value: str, source: str) -> UUID:
        """First-writer-wins, enforced by the unique(kind, value) constraint: ON
        CONFLICT DO NOTHING, then read back the current owner. Same semantics as
        the in-memory backend — never silently steal an identifier."""
        with self._connection().cursor() as cur:
            cur.execute(
                f"insert into entity_aliases ({_ALIAS_COLS}) values (%s,%s,%s,%s) "
                "on conflict (kind, value) do nothing returning entity_id",
                (entity_id, kind, value, source),
            )
            row = cur.fetchone()
            if row is not None:
                return row[0]
            cur.execute(
                "select entity_id from entity_aliases where kind = %s and value = %s",
                (kind, value),
            )
            existing = cur.fetchone()
        return existing[0] if existing else entity_id

    def find_by_alias(self, kind: str, value: str) -> UUID | None:
        with self._connection().cursor() as cur:
            cur.execute(
                "select entity_id from entity_aliases where kind = %s and value = %s",
                (kind, value),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def aliases_for(self, entity_id: UUID) -> list[Alias]:
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"select {_ALIAS_COLS} from entity_aliases where entity_id = %s "
                "order by kind, value, entity_id",
                (entity_id,),
            )
            return [Alias(**row) for row in cur.fetchall()]

    def aliases_by_kind(self, kind: str) -> list[Alias]:
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"select {_ALIAS_COLS} from entity_aliases where kind = %s "
                "order by kind, value, entity_id",
                (kind,),
            )
            return [Alias(**row) for row in cur.fetchall()]

    # -- companies ----------------------------------------------------------

    def create_company(
        self,
        name: str,
        *,
        founder_entity_ids: list[UUID] | None = None,
        archetype: int | None = None,
    ) -> Company:
        company = Company(
            name=name, founder_entity_ids=list(founder_entity_ids or []), archetype=archetype
        )
        with self._connection().cursor() as cur:
            cur.execute(
                f"insert into companies ({_COMPANY_COLS}) values (%s,%s,%s,%s,%s)",
                (
                    company.company_id,
                    company.name,
                    company.founder_entity_ids,
                    company.archetype,
                    company.created_at,
                ),
            )
        return company

    def get_company(self, company_id: UUID) -> Company | None:
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"select {_COMPANY_COLS} from companies where company_id = %s", (company_id,)
            )
            row = cur.fetchone()
        return Company(**row) if row else None

    def companies(self) -> list[Company]:
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(f"select {_COMPANY_COLS} from companies order by company_id")
            return [Company(**row) for row in cur.fetchall()]

    # -- merges -------------------------------------------------------------

    def record_merge(
        self, entity_a: UUID, entity_b: UUID, status: str, score: float, rationale: str
    ) -> Merge:
        merge = Merge(
            entity_a=entity_a, entity_b=entity_b, status=status, score=score, rationale=rationale
        )
        with self._connection().cursor() as cur:
            cur.execute(
                f"insert into merges ({_MERGE_COLS}) values (%s,%s,%s,%s,%s,%s)",
                (
                    merge.entity_a,
                    merge.entity_b,
                    merge.status,
                    merge.score,
                    merge.rationale,
                    merge.decided_at,
                ),
            )
        return merge

    def merges(self, *, status: str | None = None) -> list[Merge]:
        sql = f"select {_MERGE_COLS} from merges"
        params: list = []
        if status is not None:
            sql += " where status = %s"
            params.append(status)
        sql += " order by decided_at, entity_a, entity_b, status, score, rationale"
        with self._connection().cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [Merge(**row) for row in cur.fetchall()]

    # -- test / demo support ------------------------------------------------

    def reset(self) -> None:
        """Wipe all state. Test/demo only — TRUNCATE bypasses the row-level
        append-only trigger by design. Never call this against a database whose
        Memory you intend to keep."""
        with self._connection().cursor() as cur:
            cur.execute(
                "truncate events, entity_aliases, merges, companies, entities "
                "restart identity cascade"
            )
