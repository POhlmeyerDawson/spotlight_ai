"""Append-only event store + entity registry. Owner: A.

The public Memory contract is backend-neutral. In-memory is the deterministic
offline default; ``MEMORY_BACKEND=postgres`` selects the persistent backend.
Compatibility helpers at the bottom keep C's API/sourcing code working without
introducing a second store abstraction.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from schema.events import Company, CompanyProvenance, Entity, Event, utcnow

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
        provenance: CompanyProvenance = CompanyProvenance.SOURCED,
    ) -> Company:
        company = Company(
            name=name,
            founder_entity_ids=list(founder_entity_ids or []),
            archetype=archetype,
            provenance=provenance,
        )
        with self._lock:
            self._companies[company.company_id] = company
        return company

    def set_company_provenance(self, company_id: UUID, provenance: CompanyProvenance) -> None:
        with self._lock:
            company = self._companies.get(company_id)
            if company is not None:
                self._companies[company_id] = company.model_copy(
                    update={"provenance": provenance}
                )

    def set_company_founders(self, company_id: UUID, founder_entity_ids: list[UUID]) -> None:
        with self._lock:
            company = self._companies.get(company_id)
            if company is not None:
                self._companies[company_id] = company.model_copy(
                    update={"founder_entity_ids": list(founder_entity_ids)}
                )

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
    """Which store to read. Explicit MEMORY_BACKEND wins; otherwise INFER from the
    configured connection string.

    Defaulting to "memory" when DATABASE_URL points at Postgres is a silent
    total-blindness failure: the app reads an ephemeral in-memory store, returns
    zero events, and reports nothing wrong — measured here with 475 rows sitting in
    Supabase and every query coming back empty. MEMORY_BACKEND was set in neither
    .env nor .env.example, so that was the default path, not an edge case.
    """
    explicit = os.getenv("MEMORY_BACKEND", "").strip().lower()
    if explicit:
        return explicit

    from core.config import settings

    url = (settings.database_url or "").strip().lower()
    if url.startswith(("postgres://", "postgresql://")):
        return "postgres"
    return "memory"


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


# --- request-scoped bulk prefetch -------------------------------------------------
#
# The ranked list asks the gate and the score for every company in turn, and each of
# those calls `events(company_id=...)`. At 27 companies that was ~138 round trips and
# tolerable; at 126 it is the reason `/companies` took 48-70s and the browser fell back
# to fixtures. The queries are not slow — 25ms each — there are simply N of them, and
# every one pays a network round trip to a hosted Postgres.
#
# `prefetch` loads the whole as_of-scoped log ONCE and serves the same reads from
# memory. It is deliberately a context manager rather than a persistent cache: an
# append-only log means a long-lived cache would go stale silently, and the one thing
# worse than a slow list is a list that quietly serves yesterday's evidence.
# A ContextVar, NOT a module global. `list_companies` is a sync def, so FastAPI runs it
# on the anyio threadpool: with a plain global, two concurrent requests interleave their
# save/restore and one request's cache leaks past the end of its own `with` block,
# serving stale evidence indefinitely. `memory/score.py` already uses a ContextVar for
# the same reason. A ContextVar is per-task, so the leak cannot happen.
_prefetched: ContextVar[tuple[datetime, list[Event]] | None] = ContextVar(
    "store_prefetched", default=None
)


@contextmanager
def prefetch(as_of: datetime):
    """Serve every `events()` read in this block from one query."""
    token = _prefetched.set((as_of, get_store().events(as_of=as_of)))
    try:
        yield
    finally:
        _prefetched.reset(token)


def events(
    *,
    as_of: datetime,
    entity_id: UUID | None = None,
    company_id: UUID | None = None,
    kind: str | None = None,
) -> list[Event]:
    # Only serve from the prefetch when the cutoff MATCHES. A cached read at a
    # different as_of would be a lookahead violation (Invariant #1) — the one bug
    # class this codebase treats as unforgivable — so a mismatch falls through to
    # the store rather than filtering the cache down.
    cached = _prefetched.get()
    if cached is not None and cached[0] == as_of:
        # Copy: callers receive a list they may sort or mutate in place, and the cache
        # is shared across every read in the block.
        rows = list(cached[1])
        if entity_id is not None:
            rows = [e for e in rows if e.entity_id == entity_id]
        if company_id is not None:
            rows = [e for e in rows if e.company_id == company_id]
        if kind is not None:
            rows = [e for e in rows if str(e.kind) == str(kind)]
        return rows
    return get_store().events(as_of=as_of, entity_id=entity_id, company_id=company_id, kind=kind)


def reset() -> None:
    """Reset only the in-memory backend; never truncate a real Postgres database."""
    _default.reset()


# C compatibility helpers. These return dictionaries because the API/sourcing layer
# predates A's typed Entity/Company models. The underlying store remains singular.
def upsert_entity(name: str, normalized: str) -> UUID:
    current = next((e for e in get_store().entities() if e.name_normalized == normalized), None)
    return current.entity_id if current else get_store().create_entity(name, normalized).entity_id


def upsert_company(
    name: str,
    archetype: int | None = None,
    *,
    provenance: CompanyProvenance,
    founder_entity_ids: list[UUID] | None = None,
) -> UUID:
    """`provenance` is REQUIRED and keyword-only, deliberately.

    It used to default to SOURCED, so any caller that forgot it silently claimed the
    company's evidence was collected from the outside world. `schema/events.py` and
    migration 007 removed the same default for the same reason: when we do not know
    where a company came from, neither value is honest, so there is nothing safe to
    default to. Making it required moves the claim to the call site, where whoever
    writes it can actually be held to it.
    """
    current = next((c for c in get_store().companies() if c.name == name), None)
    if current:
        # Correct the provenance of a row that already exists rather than leaving it.
        # An early return here is how a company seeded before this field existed keeps
        # a stale `sourced` label through every subsequent reseed — a mislabelling that
        # nothing downstream can detect, on precisely the field whose job is to stop a
        # constructed company being read as sourced evidence.
        if current.provenance != provenance:
            get_store().set_company_provenance(current.company_id, provenance)
        return current.company_id
    # Populated at creation when the caller knows the founder. The column is described
    # elsewhere as a denormalized convenience, but leaving it empty is not free: every
    # reader then falls back to scanning the event log for the company's entities, which
    # on GET /companies is one extra database round trip per company on the hot path.
    return get_store().create_company(
        name,
        archetype=archetype,
        provenance=provenance,
        founder_entity_ids=founder_entity_ids,
    ).company_id


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
    """Test/demo reset. Delegates to ``reset()`` so the Postgres-safety rule lives in
    exactly one place: a reset never truncates a real database."""
    reset()


def count() -> int:
    """Total events currently visible in the log."""
    return len(events(as_of=utcnow()))


def get_event(event_id: UUID) -> Event | None:
    """Single event by id, unscoped by as_of — callers asking for a specific event
    already know which one they want."""
    for e in events(as_of=utcnow()):
        if e.event_id == event_id:
            return e
    return None
