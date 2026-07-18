"""Identity: the Founder Score belongs to the person, not the company. It persists
across companies and ideas, because it reads the entity's whole history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory import queries, score, store
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _green(eid, company_id, month, value):
    return store.append(
        Event(
            entity_id=eid,
            company_id=company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.MANUAL,
            observed_at=T0 + timedelta(days=30 * month),
            payload={"value": value, "self_consistency": 0.9},
        )
    )


def test_one_founder_two_companies_shares_a_timeline() -> None:
    s = store.get_store()
    founder = s.create_entity("Serial", "serial").entity_id
    first_co = s.create_company("FirstCo", founder_entity_ids=[founder])
    second_co = s.create_company("SecondCo", founder_entity_ids=[founder])
    _green(founder, first_co.company_id, 0, 0.6)
    _green(founder, second_co.company_id, 12, 0.85)

    tl = queries.timeline(founder, as_of=T0 + timedelta(days=400))
    companies = {e.company_id for e in tl}
    assert companies == {first_co.company_id, second_co.company_id}


def test_founder_score_persists_across_a_new_company() -> None:
    s = store.get_store()
    founder = s.create_entity("Persist", "persist").entity_id
    old_co = s.create_company("OldCo", founder_entity_ids=[founder])
    _green(founder, old_co.company_id, 0, 0.7)
    _green(founder, old_co.company_id, 3, 0.8)

    as_of = T0 + timedelta(days=120)
    before = score.founder(founder, as_of=as_of)
    # Starting a brand-new company doesn't reset the founder.
    s.create_company("NewCo", founder_entity_ids=[founder])
    after = score.founder(founder, as_of=as_of)
    assert (after.mu, after.band, after.trend) == (before.mu, before.band, before.trend)
    assert after.contributing_event_ids == before.contributing_event_ids


def test_multiple_founders_one_company_scored_independently() -> None:
    s = store.get_store()
    a = s.create_entity("Co-founder A", "co founder a").entity_id
    b = s.create_entity("Co-founder B", "co founder b").entity_id
    company = s.create_company("SharedCo", founder_entity_ids=[a, b])
    _green(a, company.company_id, 0, 0.9)
    _green(b, company.company_id, 0, 0.5)
    as_of = T0 + timedelta(days=10)
    assert score.founder(a, as_of=as_of).mu > score.founder(b, as_of=as_of).mu
