"""Entity resolution: deterministic identifiers, transliteration, and — the
point — never guessing an ambiguous merge."""

from __future__ import annotations

from memory import resolver
from memory.resolver import name_similarity, normalize_name
from schema.events import EntityCandidate, ResolutionStatus, Source


def _cand(**kw) -> EntityCandidate:
    kw.setdefault("source", Source.MANUAL)
    return EntityCandidate(**kw)


# -- normalization / transliteration ---------------------------------------


def test_transliteration_makes_cyrillic_match_its_romanization() -> None:
    assert name_similarity("Александр Иванов", "Aleksandr Ivanov") > 0.9


def test_transliteration_devanagari_and_diacritics() -> None:
    assert normalize_name("José García") == "jose garcia"
    assert name_similarity("José García", "Jose Garcia") > 0.95
    # Devanagari romanization is recognizably close, not identical spelling.
    assert name_similarity("अमित", "Amit") > 0.6


# -- deterministic identifier matches --------------------------------------


def test_exact_email_merges() -> None:
    first = resolver.resolve(_cand(name="Sam Rivera", email="sam@example.com"))
    assert first.status is ResolutionStatus.NEW
    again = resolver.resolve(_cand(name="Samuel Rivera", email="SAM@example.com"))
    assert again.status is ResolutionStatus.MERGED
    assert again.entity_id == first.entity_id


def test_github_url_normalizes_to_handle_and_merges() -> None:
    first = resolver.resolve(_cand(name="Dev One", urls=["https://github.com/DevOne"]))
    again = resolver.resolve(_cand(name="Dev One", handles={"github": "devone"}))
    assert again.status is ResolutionStatus.MERGED
    assert again.entity_id == first.entity_id


# -- the three outcomes -----------------------------------------------------


def test_unique_candidate_is_new() -> None:
    res = resolver.resolve(_cand(name="Wholly Unique Person", email="unique@nowhere.test"))
    assert res.status is ResolutionStatus.NEW


def test_same_name_different_people_is_ambiguous_not_merged() -> None:
    first = resolver.resolve(_cand(name="John Smith", email="john1@a.test"))
    second = resolver.resolve(_cand(name="John Smith", email="john2@b.test"))
    assert second.status is ResolutionStatus.AMBIGUOUS
    assert first.entity_id in second.alternatives
    assert second.entity_id != first.entity_id  # both nodes kept


def test_conflicting_strong_identifiers_are_ambiguous() -> None:
    a = resolver.resolve(_cand(name="Alpha Person", email="alpha@a.test"))
    b = resolver.resolve(_cand(name="Beta Person", handles={"github": "betaperson"}))
    # One candidate carries both identifiers, which point at two different people.
    res = resolver.resolve(
        _cand(name="Gamma", email="alpha@a.test", handles={"github": "betaperson"})
    )
    assert res.status is ResolutionStatus.AMBIGUOUS
    both = {res.entity_id, *res.alternatives}
    assert a.entity_id in both and b.entity_id in both


def test_name_plus_shared_context_merges() -> None:
    first = resolver.resolve(
        _cand(name="Priya Nair", urls=["https://github.com/openinfra/scheduler"])
    )
    assert first.status is ResolutionStatus.NEW  # only a repo context, no strong id
    again = resolver.resolve(
        _cand(name="Priya Nair", urls=["https://github.com/openinfra/scheduler"])
    )
    assert again.status is ResolutionStatus.MERGED  # name match + shared repo
    assert again.entity_id == first.entity_id


def test_ambiguous_resolution_is_recorded_for_review() -> None:
    resolver.resolve(_cand(name="Jamie Fox", email="jamie1@a.test"))
    resolver.resolve(_cand(name="Jamie Fox", email="jamie2@b.test"))
    from memory import store

    assert store.get_store().merges(status="ambiguous")  # D can surface these
