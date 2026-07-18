"""Append-only event store. Owner: A.

Note the signature: as_of is REQUIRED and has no default. That is deliberate —
it makes the lookahead bug hard to write rather than merely discouraged.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from schema.events import Event

DATA_DIR = Path("data/store")
EVENTS_FILE = DATA_DIR / "events.json"


def _load_events() -> list[Event]:
    """Load events from file or return empty list."""
    if not EVENTS_FILE.exists():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text())
        return [Event(**e) for e in data]
    except (json.JSONDecodeError, ValueError):
        return []


def _save_events(events: list[Event]) -> None:
    """Save events to file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(json.dumps([e.model_dump() for e in events], default=str))


def append(event: Event) -> UUID:
    """Append an event to the store.

    Args:
        event: Event to append

    Returns:
        The event_id of the appended event
    """
    events = _load_events()
    events.append(event)
    _save_events(events)
    return event.event_id


def events(
    *,
    as_of: datetime,
    entity_id: UUID | None = None,
    company_id: UUID | None = None,
    kind: str | None = None,
) -> list[Event]:
    """Returns only events with observed_at <= as_of. No exceptions, no flags.

    Args:
        as_of: Filter events to those with observed_at <= as_of
        entity_id: Optional filter by entity_id
        company_id: Optional filter by company_id
        kind: Optional filter by kind (string match)

    Returns:
        List of matching events sorted by observed_at ascending
    """
    events = _load_events()

    # Filter by as_of
    result = [e for e in events if e.observed_at <= as_of]

    # Filter by entity_id
    if entity_id is not None:
        result = [e for e in result if e.entity_id == entity_id]

    # Filter by company_id
    if company_id is not None:
        result = [e for e in result if e.company_id == company_id]

    # Filter by kind
    if kind is not None:
        result = [e for e in result if e.kind.value == kind]

    # Sort by observed_at ascending
    result.sort(key=lambda e: e.observed_at)

    return result


def clear() -> None:
    """Clear all events from the store (for testing)."""
    EVENTS_FILE.unlink(missing_ok=True)


def count() -> int:
    """Return the total number of events in the store."""
    return len(_load_events())


def get_event(event_id: UUID) -> Event | None:
    """Get a specific event by ID."""
    events = _load_events()
    for e in events:
        if e.event_id == event_id:
            return e
    return None
