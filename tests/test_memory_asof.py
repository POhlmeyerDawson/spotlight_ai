"""As-of and no-lookahead contract for the integrated Memory layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backtest.runner import assert_no_lookahead
from memory import queries, score, store
from schema.events import CompanyProvenance, Event, EventKind, Source

T1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=30)
T3 = T1 + timedelta(days=60)


def _event(observed_at: datetime, **kwargs) -> Event:
    return Event(
        kind=kwargs.pop("kind", EventKind.REPO_ACTIVITY),
        source=kwargs.pop("source", Source.GITHUB),
        observed_at=observed_at,
        **kwargs,
    )


def _green(entity_id, observed_at, value) -> Event:
    return Event(
        entity_id=entity_id,
        kind=EventKind.GREEN_FLAG,
        source=Source.MANUAL,
        observed_at=observed_at,
        payload={"value": value, "self_consistency": 0.9},
    )


def _seed_three():
    entity = store.get_store().create_entity("T", "t")
    e1 = store.append(_green(entity.entity_id, T1, 0.6))
    e2 = store.append(_green(entity.entity_id, T2, 0.7))
    e3 = store.append(_green(entity.entity_id, T3, 0.8))
    return entity.entity_id, e1, e2, e3


def test_events_at_t2_never_return_the_t3_event() -> None:
    entity_id, e1, e2, e3 = _seed_three()
    got = store.events(entity_id=entity_id, as_of=T2)
    ids = {event.event_id for event in got}
    assert e1 in ids and e2 in ids and e3 not in ids
    assert all(event.observed_at <= T2 for event in got)
    assert_no_lookahead(got, as_of=T2)


def test_founder_score_at_t2_excludes_the_future_event() -> None:
    entity_id, e1, e2, e3 = _seed_three()
    result = score.founder(entity_id, as_of=T2)
    assert e1 in result.contributing_event_ids
    assert e2 in result.contributing_event_ids
    assert e3 not in result.contributing_event_ids


def test_later_as_of_admits_the_later_event() -> None:
    entity_id, _e1, _e2, e3 = _seed_three()
    assert e3 in score.founder(entity_id, as_of=T3).contributing_event_ids


def test_events_are_ordered_by_observed_at() -> None:
    entity_id = uuid4()
    for timestamp in (T3, T1, T2):
        store.append(_event(timestamp, entity_id=entity_id))
    assert [event.observed_at for event in store.events(as_of=T3, entity_id=entity_id)] == [
        T1,
        T2,
        T3,
    ]


def test_boundary_is_inclusive_and_timezone_round_trips() -> None:
    entity_id = uuid4()
    observed = datetime(2024, 1, 4, 5, 6, 7, 891234, tzinfo=timezone.utc)
    original = _event(observed, entity_id=entity_id, payload={"repo": "x/y"}, confidence=0.4)
    store.append(original)
    got = store.events(as_of=observed, entity_id=entity_id)[0]
    assert got.observed_at == observed
    assert got.ingested_at == original.ingested_at
    assert got.payload == {"repo": "x/y"}
    assert got.confidence == pytest.approx(0.4)
    assert store.events(as_of=observed - timedelta(microseconds=1), entity_id=entity_id) == []


def test_entity_company_and_kind_filters() -> None:
    entity_one, entity_two, company = uuid4(), uuid4(), uuid4()
    store.append(_event(T1, entity_id=entity_one, company_id=company))
    store.append(_event(T1, entity_id=entity_two))
    store.append(_event(T1, company_id=company, kind=EventKind.DECK_CLAIM, source=Source.DECK))
    assert len(store.events(as_of=T3, entity_id=entity_one)) == 1
    assert len(store.events(as_of=T3, company_id=company)) == 2
    assert len(store.events(as_of=T3, company_id=company, kind=EventKind.DECK_CLAIM)) == 1
    assert len(store.events(as_of=T3)) == 3


def test_upsert_entity_and_company_helpers_are_idempotent() -> None:
    first = store.upsert_entity("Ólafur Þórðarson", "olafur thordarson")
    assert store.upsert_entity("Olafur Thordarson", "olafur thordarson") == first
    assert store.get_entity(first)["display_name"] == "Ólafur Þórðarson"
    company = store.upsert_company("Acme", archetype=2, provenance=CompanyProvenance.SOURCED)
    assert store.upsert_company("Acme", provenance=CompanyProvenance.SOURCED) == company
    assert store.get_company(company)["archetype"] == 2
    assert len(store.all_entities()) == 1
    assert len(store.all_companies()) == 1


def test_queries_are_as_of_scoped() -> None:
    entity_id, company_id = uuid4(), uuid4()
    store.append(_event(T1, entity_id=entity_id))
    store.append(_event(T3, entity_id=entity_id))
    store.append(_event(T1, company_id=company_id, kind=EventKind.DECK_CLAIM, source=Source.DECK))
    store.append(_event(T3, company_id=company_id, kind=EventKind.DECK_CLAIM, source=Source.DECK))
    store.append(_event(T1, company_id=company_id, kind=EventKind.HN_POST, source=Source.HN))
    assert len(queries.timeline(entity_id, as_of=T2)) == 1
    assert len(queries.claims(company_id, as_of=T2)) == 1


def test_event_store_has_no_mutation_surface() -> None:
    assert not hasattr(store.get_store(), "update_event")
    assert not hasattr(store.get_store(), "delete_event")
