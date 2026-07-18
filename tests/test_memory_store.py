"""Append-only event store: persistence, provenance, filtering, ordering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory import store
from schema.events import Event, EventKind, Source

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _event(**kw) -> Event:
    base = dict(kind=EventKind.REPO_ACTIVITY, source=Source.GITHUB, observed_at=T0)
    base.update(kw)
    return Event(**base)


def test_append_returns_id_and_read_back() -> None:
    eid = store.append(_event())
    got = store.events(as_of=T0)
    assert [e.event_id for e in got] == [eid]


def test_provenance_is_preserved() -> None:
    store.append(
        _event(
            source_url="https://github.com/x/y",
            evidence_span="commit deadbeef",
            integrity_flags=["ocr_low_conf"],
            confidence=0.42,
        )
    )
    e = store.events(as_of=T0)[0]
    assert e.source_url == "https://github.com/x/y"
    assert e.evidence_span == "commit deadbeef"
    assert e.integrity_flags == ["ocr_low_conf"]
    assert e.confidence == 0.42
    assert e.source == Source.GITHUB


def test_store_exposes_no_mutation_of_the_log() -> None:
    """Append-only by construction: there is no update/delete for events."""
    assert not hasattr(store.get_store(), "update_event")
    assert not hasattr(store.get_store(), "delete_event")


def test_correction_is_a_new_event_not_an_overwrite() -> None:
    ent = store.get_store().create_entity("N", "n")
    store.append(
        _event(
            entity_id=ent.entity_id,
            kind=EventKind.PROFILE_FACT,
            observed_at=T0,
            payload={"key": "role", "value": "eng"},
        )
    )
    store.append(
        _event(
            entity_id=ent.entity_id,
            kind=EventKind.PROFILE_FACT,
            observed_at=T0 + timedelta(days=1),
            payload={"key": "role", "value": "cto"},
        )
    )
    facts = store.events(entity_id=ent.entity_id, as_of=T0 + timedelta(days=2))
    assert len(facts) == 2  # both survive; the old fact is not erased


def test_filters_by_entity_company_and_kind() -> None:
    a = store.get_store().create_entity("A", "a")
    b = store.get_store().create_entity("B", "b")
    co = store.get_store().create_company("Co")
    store.append(_event(entity_id=a.entity_id, company_id=co.company_id, kind=EventKind.RELEASE))
    store.append(_event(entity_id=b.entity_id, kind=EventKind.HN_POST, source=Source.HN))
    assert len(store.events(as_of=T0, entity_id=a.entity_id)) == 1
    assert len(store.events(as_of=T0, company_id=co.company_id)) == 1
    assert len(store.events(as_of=T0, kind=EventKind.HN_POST)) == 1
    assert len(store.events(as_of=T0, kind=EventKind.RELEASE)) == 1


def test_kind_filter_accepts_enum_and_str() -> None:
    store.append(_event(kind=EventKind.PAPER, source=Source.ARXIV))
    assert len(store.events(as_of=T0, kind=EventKind.PAPER)) == 1
    assert len(store.events(as_of=T0, kind="paper")) == 1


def test_results_sorted_by_observed_at() -> None:
    store.append(_event(observed_at=T0 + timedelta(days=5)))
    store.append(_event(observed_at=T0 + timedelta(days=1)))
    store.append(_event(observed_at=T0 + timedelta(days=3)))
    got = store.events(as_of=T0 + timedelta(days=10))
    times = [e.observed_at for e in got]
    assert times == sorted(times)


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        store.events(as_of=datetime(2024, 1, 1))  # noqa: DTZ001 — the point of the test
