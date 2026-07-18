"""Entity resolution, and Type 6 in particular: transliterated names must not vanish,
and two real people with the same name must never be silently fused."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from memory import db, resolver, store
from memory.resolver import name_similarity, normalize_name
from schema.events import EntityCandidate, Event, EventKind, ResolutionStatus, Source

T2015 = datetime(2015, 1, 1, tzinfo=timezone.utc)
T2023 = datetime(2023, 1, 1, tzinfo=timezone.utc)
WIDE = datetime(2100, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    yield
    db.reset_connections()


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


def _candidate(name: str | None = None, **kw) -> EntityCandidate:
    return EntityCandidate(name=name, source=kw.pop("source", Source.GITHUB), **kw)


# --- Type 6: the name signal itself -----------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("Дмитрий Иванов", "Dmitry Ivanov"),
        ("李伟", "Li Wei"),
        ("Sreenivasan", "Srinivasan"),
        ("Ольга Петрова", "Olga Petrova"),
        ("राजेश कुमार", "Rajesh Kumar"),
    ],
)
def test_transliterated_names_are_similar(a: str, b: str) -> None:
    assert normalize_name(a).isascii()
    assert name_similarity(a, b) > 0.85


def test_name_order_and_diacritics_do_not_matter() -> None:
    assert name_similarity("Ólafur Þórðarson", "Thordarson Olafur") > 0.9
    assert name_similarity("Wei Zhang", "Jane Doe") < resolver.NAME_FLOOR  # contributes nothing


# --- MERGED ------------------------------------------------------------------


def test_transliterated_identity_merges_on_shared_handle() -> None:
    first = resolver.resolve(_candidate("Dmitry Ivanov", handles={"github": "dmitry-i"}))
    assert first.status is ResolutionStatus.NEW

    got = resolver.resolve(
        _candidate("Дмитрий Иванов", urls=["https://github.com/dmitry-i"])
    )
    assert got.status is ResolutionStatus.MERGED
    assert got.entity_id == first.entity_id
    assert "name similarity" in got.rationale and "handle" in got.rationale


def test_exact_email_merges_even_when_the_name_is_written_differently() -> None:
    first = resolver.resolve(_candidate("Sreenivasan Raghavan", email="sri@example.com"))
    got = resolver.resolve(
        _candidate("S. Srinivasan", email="SRI@example.com", source=Source.HN)
    )
    assert got.status is ResolutionStatus.MERGED
    assert got.entity_id == first.entity_id
    assert got.score > 0.85
    assert "exact email match" in got.rationale


def test_resolve_is_idempotent() -> None:
    c = _candidate("Li Wei", handles={"github": "liwei"})
    assert resolver.resolve(c).entity_id == resolver.resolve(c).entity_id


# --- NEW: two different people who happen to share a name --------------------


def test_two_distinct_wei_zhangs_never_silently_merge() -> None:
    older = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-robotics"}))
    _event(T2015, {"github": "wz-robotics", "repo": "wz/arm"}, entity_id=older.entity_id)
    _event(T2015 + timedelta(days=200), {"github": "wz-robotics"}, entity_id=older.entity_id)

    # a different Wei Zhang: different handle, activity starting eight years later
    _event(T2023, {"github": "weizhang-nlp", "repo": "nlp/tok"})
    got = resolver.resolve(_candidate("Wei Zhang", handles={"github": "weizhang-nlp"}))

    assert got.status is not ResolutionStatus.MERGED
    assert got.entity_id != older.entity_id
    assert "disjoint" in got.rationale


# --- AMBIGUOUS: the point ----------------------------------------------------


def test_ambiguous_populates_alternatives_and_picks_nobody() -> None:
    existing = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-a"}))

    # same name, no shared identifier, no era evidence either way -> inside the band
    got = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-b"}))

    assert got.status is ResolutionStatus.AMBIGUOUS
    assert 0.4 <= got.score <= 0.85
    assert got.alternatives == [existing.entity_id]
    assert got.entity_id not in got.alternatives  # kept separate, not guessed into one
    assert "could not confirm" in got.rationale

    merges = db.connect().execute("select * from merges where status = 'ambiguous'").fetchall()
    assert len(merges) == 1
    assert merges[0]["entity_b"] == str(existing.entity_id)


def test_ambiguous_writes_an_entity_merge_event_for_the_memo() -> None:
    resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-a"}))
    got = resolver.resolve(_candidate("Wei Zhang", handles={"github": "wz-b"}))

    events = store.events(as_of=WIDE, kind=EventKind.ENTITY_MERGE)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["status"] == "ambiguous"
    assert payload["alternatives"] == [str(a) for a in got.alternatives]
    assert payload["rationale"] == got.rationale


# --- co-occurrence -----------------------------------------------------------


def test_co_occurrence_raises_the_score() -> None:
    existing = resolver.resolve(_candidate("Li Wei", handles={"github": "liwei"}))
    _event(T2023, {"github": "liwei", "repo": "acme/engine"}, entity_id=existing.entity_id)
    _event(T2023, {"github": "lwei", "repo": "acme/engine"})  # same repo, other handle

    got = resolver.resolve(_candidate("李伟", handles={"github": "lwei"}))
    assert got.status is ResolutionStatus.AMBIGUOUS
    assert "co-occurrence in acme/engine" in got.rationale

    baseline = resolver.resolve(_candidate("李伟", handles={"github": "lwei-2"}))
    assert got.score > baseline.score


# --- NEW ---------------------------------------------------------------------


def test_unrelated_candidate_is_new() -> None:
    resolver.resolve(_candidate("Dmitry Ivanov", handles={"github": "dmitry-i"}))
    got = resolver.resolve(_candidate("Jane Okonkwo", email="jane@example.com"))
    assert got.status is ResolutionStatus.NEW
    assert got.alternatives == []
    assert store.get_entity(got.entity_id)["display_name"] == "Jane Okonkwo"
    assert store.events(as_of=WIDE, kind=EventKind.ENTITY_MERGE) == []
