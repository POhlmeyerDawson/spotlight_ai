"""Append-only event store + entity registry. Owner: A.

The public Memory contract is backend-neutral. In-memory is the deterministic
offline default; ``MEMORY_BACKEND=postgres`` selects the persistent backend.
Compatibility helpers at the bottom keep C's API/sourcing code working without
introducing a second store abstraction.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from schema.events import Company, Entity, Event, utcnow

if TYPE_CHECKING:
    from memory.pg_store import PostgresEventStore


@dataclass(frozen=True)
class Alias:
    entity_id: UUID
    kind: str
    value: str
    source: str


@dataclass(frozen=True)
class Merge:
    entity_a: UUID
    entity_b: UUID
    status: str
    score: float
    rationale: str
    decided_at: datetime = field(default_factory=utcnow)


class EventStore:
    """Append-only event log plus entity/company/alias/merge registries."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[Event] = []
        self._entities: dict[UUID, Entity] = {}
        self._companies: dict[UUID, Company] = {}
        self._aliases: dict[tuple[str, str], Alias] = {}
        self._merges: list[Merge] = []

    def append(self, event: Event) -> UUID:
        """Append once by event ID; re-offering an existing event is a no-op."""
        with self._lock:
            if any(existing.event_id == event.event_id for existing in self._events):
                return event.event_id
            self._events.append(event)
        return event.event_id

    def events(
        self,
        *,
        as_of: datetime,
        entity_id: UUID | None = None,
        company_id: UUID | None = None,
        kind: str | None = None,
    ) -> list[Event]:
        """Return only events with observed_at <= as_of, in deterministic order."""
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware — a naive as_of silently mis-filters")
        with self._lock:
            out = [
                e
                for e in self._events
                if e.observed_at <= as_of
                and (entity_id is None or e.entity_id == entity_id)
                and (company_id is None or e.company_id == company_id)
                and (kind is None or e.kind == kind)
            ]
        out.sort(key=lambda e: (e.observed_at, e.ingested_at, str(e.event_id)))
        return out

    def create_entity(self, display_name: str, name_normalized: str) -> Entity:
        entity = Entity(display_name=display_name, name_normalized=name_normalized)
        with self._lock:
            self._entities[entity.entity_id] = entity
        return entity

    def get_entity(self, entity_id: UUID) -> Entity | None:
        return self._entities.get(entity_id)

    def entities(self) -> list[Entity]:
        with self._lock:
            return sorted(self._entities.values(), key=lambda e: str(e.entity_id))

    def add_alias(self, entity_id: UUID, kind: str, value: str, source: str) -> UUID:
        """First writer wins for each (kind, value), matching the SQL constraint."""
        key = (kind, value)
        with self._lock:
            existing = self._aliases.get(key)
            if existing is not None:
                return existing.entity_id
            self._aliases[key] = Alias(entity_id, kind, value, source)
        return entity_id

    def find_by_alias(self, kind: str, value: str) -> UUID | None:
        alias = self._aliases.get((kind, value))
        return alias.entity_id if alias else None

    def aliases_for(self, entity_id: UUID) -> list[Alias]:
        with self._lock:
            aliases = [a for a in self._aliases.values() if a.entity_id == entity_id]
        return sorted(aliases, key=lambda a: (a.kind, a.value, str(a.entity_id)))

    def aliases_by_kind(self, kind: str) -> list[Alias]:
        with self._lock:
            aliases = [a for a in self._aliases.values() if a.kind == kind]
        return sorted(aliases, key=lambda a: (a.kind, a.value, str(a.entity_id)))

    def create_company(
        self,
        name: str,
        *,
        founder_entity_ids: list[UUID] | None = None,
        archetype: int | None = None,
    ) -> Company:
        company = Company(
            name=name,
            founder_entity_ids=list(founder_entity_ids or []),
            archetype=archetype,
        )
        with self._lock:
            self._companies[company.company_id] = company
        return company

    def get_company(self, company_id: UUID) -> Company | None:
        return self._companies.get(company_id)

    def companies(self) -> list[Company]:
        with self._lock:
            return sorted(self._companies.values(), key=lambda c: str(c.company_id))

    def record_merge(
        self, entity_a: UUID, entity_b: UUID, status: str, score: float, rationale: str
    ) -> Merge:
        merge = Merge(entity_a, entity_b, status, score, rationale)
        with self._lock:
            self._merges.append(merge)
        return merge

    def merges(self, *, status: str | None = None) -> list[Merge]:
        with self._lock:
            merges = [m for m in self._merges if status is None or m.status == status]
        return sorted(
            merges,
            key=lambda m: (
                m.decided_at,
                str(m.entity_a),
                str(m.entity_b),
                m.status,
                m.score,
                m.rationale,
            ),
        )

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._entities.clear()
            self._companies.clear()
            self._aliases.clear()
            self._merges.clear()


_default = EventStore()
_pg: PostgresEventStore | None = None


def _backend() -> str:
    return os.getenv("MEMORY_BACKEND", "memory").strip().lower()


def get_store() -> EventStore | PostgresEventStore:
    backend = _backend()
    if backend == "memory":
        return _default
    if backend == "postgres":
        return _get_pg_store()
    raise ValueError(f"unknown MEMORY_BACKEND={backend!r} (expected 'memory' or 'postgres')")


def _get_pg_store() -> PostgresEventStore:
    global _pg
    if _pg is None:
        from core.config import settings
        from memory.pg_store import PostgresEventStore

        if not settings.database_url:
            raise RuntimeError("MEMORY_BACKEND=postgres requires DATABASE_URL to be configured")
        _pg = PostgresEventStore(settings.database_url)
    return _pg


def append(event: Event) -> UUID:
    return get_store().append(event)


def events(
    *,
    as_of: datetime,
    entity_id: UUID | None = None,
    company_id: UUID | None = None,
    kind: str | None = None,
) -> list[Event]:
    return get_store().events(as_of=as_of, entity_id=entity_id, company_id=company_id, kind=kind)


def reset() -> None:
    """Reset only the in-memory backend; never truncate a real Postgres database."""
    _default.reset()


# C compatibility helpers. These return dictionaries because the API/sourcing layer
# predates A's typed Entity/Company models. The underlying store remains singular.
def upsert_entity(name: str, normalized: str) -> UUID:
    current = next((e for e in get_store().entities() if e.name_normalized == normalized), None)
    return current.entity_id if current else get_store().create_entity(name, normalized).entity_id


def upsert_company(name: str, archetype: int | None = None) -> UUID:
    current = next((c for c in get_store().companies() if c.name == name), None)
    if current:
        return current.company_id
    return get_store().create_company(name, archetype=archetype).company_id


def get_entity(entity_id: UUID) -> dict | None:
    entity = get_store().get_entity(entity_id)
    return entity.model_dump(mode="json") if entity else None


def get_company(company_id: UUID) -> dict | None:
    company = get_store().get_company(company_id)
    return company.model_dump(mode="json") if company else None


def all_entities() -> list[dict]:
    return [entity.model_dump(mode="json") for entity in get_store().entities()]


def all_companies() -> list[dict]:
    return [company.model_dump(mode="json") for company in get_store().companies()]


# --- API surface re-exported by memory/__init__.py (branch A's public contract) ---
#
# Implemented on top of the backed store rather than dropped: the merge brought A's
# __init__ with these names, and trimming it would have broken A's callers to make
# the import work. Kept small and honest about what they cost.


def clear() -> None:
    """Drop every event. Test/demo reset — the append-only trigger blocks DELETE, so
    this recreates the table rather than deleting rows."""
    conn = db.connect()
    conn.execute("drop table if exists events cascade")
    db.reset_connections()
    db.apply_migrations() if hasattr(db, "apply_migrations") else db.connect()


def count() -> int:
    conn = db.connect()
    row = conn.execute("select count(*) as n from events").fetchone()
    return int(row["n"] if isinstance(row, dict) else row[0])


def get_event(event_id: UUID) -> Event | None:
    """Single event by id, unscoped by as_of — callers asking for a specific event
    already know which one they want."""
    from schema.events import utcnow

    for e in events(as_of=utcnow()):
        if e.event_id == event_id:
            return e
    return None
