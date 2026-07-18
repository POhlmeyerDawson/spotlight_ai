"""as_of-scoped read helpers for C and D. Owner: A."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from memory import store
from schema.events import Event, EventKind


def timeline(entity_id: UUID, as_of: datetime) -> list[Event]:
    return store.events(as_of=as_of, entity_id=entity_id)


def claims(company_id: UUID, as_of: datetime) -> list[Event]:
    """DECK_CLAIM events awaiting validation."""
    return store.events(as_of=as_of, company_id=company_id, kind=EventKind.DECK_CLAIM)
