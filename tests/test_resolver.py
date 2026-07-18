"""Entity resolution: aliases, transliteration, ambiguity, and C signals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from memory import resolver, store
from memory.resolver import name_similarity, normalize_name
from schema.events import EntityCandidate, Event, EventKind, ResolutionStatus, Source

T2015 = datetime(2015, 1, 1, tzinfo=timezone.utc)
T2023 = datetime(2023, 1, 1, tzinfo=timezone.utc)
WIDE = datetime(2100, 1, 1, tzinfo=timezone.utc)


def _candidate(name: str | None = None, **kwargs) -> EntityCandidate:
    return EntityCandidate(name=name, source=kwargs.pop("source", Source.GITHUB), **kwargs)


def _event(observed_at: datetime, payload: dict, entity_id: UUID | None = None) -> None:
    store.append(
        Event(
            entity_id=entity_id,
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            observed_at=observed_at,
            payload=payload,
        )
    )


@pytest.mark.parametrize(
    "a,b",
    [
        ("Александр Иванов", "Aleksandr Ivanov"),
        ("Дмитрий Иванов", "Dmitry Ivanov"),
        ("李伟", "Li Wei"),
        ("राजेश कुमार", "Rajesh Kumar"),
    ],
)
def test_transliterated_names_are_similar(a: str, b: str) -> None:
    assert normalize_name(a).isascii()
    assert name_similarity(a, b) > 0.85


def test_name_order_and_diacritics_do_not_matter() -> None:
    assert normalize_name("José García") == "jose garcia"
    assert name_similarity("Ólafur Þórðarson", "Thordarson Olafur") > 0.9
    assert name_similarity("Wei Zhang", "Jane Doe") < resolver.NAME_FLOOR


def test_exact_email_merges_and_normalizes() -> None:
    first = resolver.resolve(_candidate("Sam Rivera", email="sam@example.com"))
    again = resolver.resolve(_candidate("Samuel Rivera", email="SAM@example.com"))
    assert first.status is ResolutionStatus.NEW
    assert again.status is ResolutionStatus.MERGED
    assert again.entity_id == first.entity_id
    assert "exact email match" in again.rationale


def test_github_url_normalizes_to_handle_and_merges() -> None:
    first = resolver.resolve(_candidate("Dev One", urls=["https://github.com/DevOne"]))
    again = resolver.resolve(_candidate("Dev One", handles={"github": "devone"}))
    assert again.status is ResolutionStatus.MERGED
    assert again.entity_id == first.entity_id


def test_transliterated_identity_merges_on_shared_handle() -> None:
    first = resolver.resolve(_candidate("Dmitry Ivanov", handles={"github": "dmitry-i"}))
    got = resolver.resolve(_candidate("Дмитрий Иванов", urls=["https://github.com/dmitry-i"]))
    assert got.status is ResolutionStatus.MERGED
    assert got.entity_id == first.entity_id
    assert "name similarity" in got.rationale and "handle" in got.rationale


def test_unique_candidate_is_new() -> None:
    result = resolver.resolve(_candidate("Wholly Unique Person", email="unique@nowhere.test"))
    assert result.status is ResolutionStatus.NEW


def test_same_name_different_people_is_ambiguous_not_merged() -> None:
    first = resolver.resolve(_candidate("John Smith", email="john1@a.test"))
    second = resolver.resolve(_candidate("John Smith", email="john2@b.test"))
    assert second.status is ResolutionStatus.AMBIGUOUS
    assert first.entity_id in second.alternatives
    assert second.entity_id != first.entity_id


def test_conflicting_strong_identifiers_are_ambiguous() -> None:
    alpha = resolver.resolve(_candidate("Alpha Person", email="alpha@a.test"))
    beta = resolver.resolve(_candidate("Beta Person", handles={"github": "betaperson"}))
    result = resolver.resolve(
        _candidate("Gamma", email="alpha@a.test", handles={"github": "betaperson"})
    )
    assert result.status is ResolutionStatus.AMBIGUOUS
    assert {result.entity_id, *result.alternatives} >= {alpha.entity_id, beta.entity_id}


def test_name_plus_shared_context_merges() -> None:
    first = resolver.resolve(
        _candidate("Priya Nair", urls=["https://github.com/openinfra/scheduler"])
    )
    again = resolver.resolve(
        _candidate("Priya Nair", urls=["https://github.com/openinfra/scheduler"])
    )
    assert first.status is ResolutionStatus.NEW
    assert again.status is ResolutionStatus.MERGED
    assert again.entity_id == first.entity_id


def test_ambiguous_resolution_is_recorded_for_review() -> None:
    resolver.resolve(_candidate("Jamie Fox", email="jamie1@a.test"))
    resolver.resolve(_candidate("Jamie Fox", email="jamie2@b.test"))
    assert store.get_store().merges(status="ambiguous")


def test_resolve_is_idempotent() -> None:
    candidate = _candidate("Li Wei", handles={"github": "liwei"})
    assert resolver.resolve(candidate).entity_id == resolver.resolve(candidate).entity_id


def test_two_distinct_wei_zhangs_never_silently_merge() -> None:
    older = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-robotics"}))
    _event(T2015, {"github": "wz-robotics", "repo": "wz/arm"}, older.entity_id)
    _event(T2015 + timedelta(days=200), {"github": "wz-robotics"}, older.entity_id)
    _event(T2023, {"github": "weizhang-nlp", "repo": "nlp/tok"})
    got = resolver.resolve(_candidate("Wei Zhang", handles={"github": "weizhang-nlp"}))
    assert got.status is not ResolutionStatus.MERGED
    assert got.entity_id != older.entity_id
    assert "disjoint" in got.rationale


def test_ambiguous_populates_alternatives_and_writes_merge_event() -> None:
    existing = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-a"}))
    got = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-b"}))
    assert got.status is ResolutionStatus.AMBIGUOUS
    assert 0.4 <= got.score <= 0.85
    assert got.alternatives == [existing.entity_id]
    assert got.entity_id not in got.alternatives
    assert "could not confirm" in got.rationale
    events = store.events(as_of=WIDE, kind=EventKind.ENTITY_MERGE)
    assert len(events) == 1
    assert events[0].payload["rationale"] == got.rationale


def test_co_occurrence_raises_the_score_without_forcing_a_merge() -> None:
    existing = resolver.resolve(_candidate("Li Wei", handles={"github": "liwei"}))
    _event(T2023, {"github": "liwei", "repo": "acme/engine"}, existing.entity_id)
    _event(T2023, {"github": "lwei", "repo": "acme/engine"})
    got = resolver.resolve(_candidate("李伟", handles={"github": "lwei"}))
    baseline = resolver.resolve(_candidate("李伟", handles={"github": "lwei-2"}))
    assert got.status is ResolutionStatus.AMBIGUOUS
    assert "co-occurrence in acme/engine" in got.rationale
    assert got.score > baseline.score


def test_unrelated_candidate_is_new_without_merge_event() -> None:
    resolver.resolve(_candidate("Dmitry Ivanov", handles={"github": "dmitry-i"}))
    got = resolver.resolve(_candidate("Jane Okonkwo", email="jane@example.com"))
    assert got.status is ResolutionStatus.NEW
    assert got.alternatives == []
    assert store.get_entity(got.entity_id)["display_name"] == "Jane Okonkwo"
    assert store.events(as_of=WIDE, kind=EventKind.ENTITY_MERGE) == []
