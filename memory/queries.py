"""As-of-scoped read helpers shared by scoring and intelligence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from memory import store
from schema.events import Event, EventKind


def timeline(entity_id: UUID, as_of: datetime) -> list[Event]:
    return store.events(as_of=as_of, entity_id=entity_id)


def company_timeline(company_id: UUID, as_of: datetime) -> list[Event]:
    return store.events(as_of=as_of, company_id=company_id)


def claims(company_id: UUID, as_of: datetime) -> list[Event]:
    return store.events(as_of=as_of, company_id=company_id, kind=EventKind.DECK_CLAIM)


def latest_facts(entity_id: UUID, as_of: datetime) -> dict[str, Event]:
    facts: dict[str, Event] = {}
    for event in store.events(entity_id=entity_id, as_of=as_of, kind=EventKind.PROFILE_FACT):
        key = str(event.payload.get("key", event.event_id))
        current = facts.get(key)
        if current is None or event.observed_at >= current.observed_at:
            facts[key] = event
    return facts


def contradicted_event_ids(entity_id: UUID, as_of: datetime) -> set[UUID]:
    store_backend = store.get_store()
    contradicted: set[UUID] = set()
    for event in store_backend.events(
        entity_id=entity_id, as_of=as_of, kind=EventKind.CONTRADICTION
    ):
        contradicted.update(_targets(event))
    for event in store_backend.events(
        entity_id=entity_id, as_of=as_of, kind=EventKind.VALIDATION_RESULT
    ):
        if str(event.payload.get("status", "")).lower() == "contradicted":
            contradicted.update(_targets(event))
    return contradicted


def _targets(event: Event) -> list[UUID]:
    raw: list[object] = []
    for key in ("target_event_id", "target_event_ids", "contradicts"):
        value = event.payload.get(key)
        if isinstance(value, list):
            raw.extend(value)
        elif value is not None:
            raw.append(value)
    targets: list[UUID] = []
    for item in raw:
        try:
            targets.append(item if isinstance(item, UUID) else UUID(str(item)))
        except (ValueError, AttributeError):
            continue
    return targets
