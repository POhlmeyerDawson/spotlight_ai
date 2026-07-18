"""as_of-scoped read helpers for C and D. Owner: A."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from schema.events import Event


def timeline(entity_id: UUID, as_of: datetime) -> list[Event]:
    raise NotImplementedError("A: H3-8")


def claims(company_id: UUID, as_of: datetime) -> list[Event]:
    """DECK_CLAIM events awaiting validation."""
    raise NotImplementedError("A: H3-8")
