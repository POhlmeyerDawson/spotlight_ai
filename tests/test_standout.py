"""api/standout.py — the comparative "what stood out" card.

Offline. The LLM is mocked everywhere; nothing here dials out.

The tests are grouped by the property they defend, and the four that matter most are:

  * `test_no_url_reaches_the_model` — the anti-hallucination mechanism itself.
  * `test_sparse_company_manufactures_nothing` — the cs-veritanode case. A summariser
    that finds something remarkable about every company is broken.
  * `test_findings_are_not_identical_across_the_corpus` — the discrimination test. This
    codebase has four times shipped a metric that returned the same confident answer for
    everybody, so a card generator gets an explicit guard against being the fifth.
  * `test_ungrounded_sentence_is_dropped_not_shown` — an assertion that cannot be
    resolved is deleted, never displayed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from api import standout
from schema.events import CompanyProvenance, Event, EventKind


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# A synthetic corpus. Small, but it has the shape that matters: one company with a
# broad public footprint, several ordinary ones, and one with nothing but a deck.
# ---------------------------------------------------------------------------


def _event(company_id, entity_id, kind, source, *, days_ago, span, url=None, payload=None):
    return Event(
        entity_id=entity_id,
        company_id=company_id,
        kind=kind,
        source=source,
        source_url=url,
        observed_at=NOW - timedelta(days=days_ago),
        payload=payload or {},
        evidence_span=span,
        confidence=0.9,
        integrity_flags=[],
    )


@pytest.fixture
def corpus(monkeypatch, tmp_path):
    """Three companies in the store, and a cache dir that is thrown away after."""
    from memory import store

    monkeypatch.setenv("VCBRAIN_STANDOUT_CACHE", str(tmp_path / "standout"))
    standout.reset_cache()

    rows = []
    for name, n_repo, has_public in (
        ("Broad Corp", 14, True),
        ("Middle Corp", 4, True),
        ("Deck Only Corp", 0, False),
    ):
        cid = store.upsert_company(name, archetype=1, provenance=CompanyProvenance.SOURCED)
        eid = uuid4()
        if has_public:
            for i in range(n_repo):
                store.append(
                    _event(
                        cid,
                        eid,
                        EventKind.REPO_ACTIVITY,
                        "github",
                        days_ago=20 * i + 3,
                        span=f'commit 4b91e0{i} "pagekv: refcounted physical pages"',
                        url=f"https://github.com/{name.split()[0].lower()}/core/commit/4b91e0{i}",
                        payload={"repo": f"{name.split()[0].lower()}/core", "commits_30d": 12 + i},
                    )
                )
        store.append(
            _event(
                cid,
                eid,
                EventKind.DECK_CLAIM,
                "deck",
                days_ago=10,
                span='slide 6: "we serve enterprise workloads"',
                payload={"claim": "enterprise workloads", "slide": 6},
            )
        )
        rows.append({"company_id": cid, "name": name, "slug": name})

    monkeypatch.setattr(standout, "_in_scope_companies", lambda: rows)
    yield rows
    standout.reset_cache()


@pytest.fixture
def broad(corpus):
    return corpus[0]["company_id"]


@pytest.fixture
def sparse(corpus):
    return corpus[2]["company_id"]


@pytest.fixture(autouse=True)
def _resolve_ids(monkeypatch):
    """company_uuid() normally walks the fixture slugs; here ids are already UUIDs."""
    from api.routers import deps

    monkeypatch.setattr(deps, "company_uuid", lambda v: deps.as_uuid(v))


# ---------------------------------------------------------------------------
# 1. Distinctiveness is COMPUTED, and it is comparative.
# ---------------------------------------------------------------------------


def test_frame_covers_the_whole_in_scope_corpus(corpus):
    fr = standout.frame(NOW, refresh=True)
    assert len(fr.companies) == 3
    assert {f.name for f in fr.companies} == {"Broad Corp", "Middle Corp", "Deck Only Corp"}


def test_rarity_denominator_is_applicability_not_corpus_size(corpus):
    """A rule that needs GitHub says nothing about a company we have no GitHub for."""
    fr = standout.frame(NOW, refresh=True)
    for rule_id in sorted({r for f in fr.companies for r in f.applicable_rules}):
        n_fired, n_applicable = fr.rule_rarity(rule_id)
        assert n_applicable <= len(fr.companies)
        assert n_fired <= n_applicable
        # Every company counted in the numerator must be in the denominator.
        for f in fr.companies:
            if rule_id in f.fired_rules:
                assert rule_id in f.applicable_rules


def test_distinctives_carry_their_corpus_comparison(broad, corpus):
    for d in standout.distinctives(broad, NOW):
        assert d.comparison.strip(), f"{d.key} states no comparison"
        # A comparative finding has to name the field it is compared against.
        assert any(w in d.comparison for w in ("of the", "median", "corpus")), d.comparison
        assert 0.0 <= d.strength <= 1.0


def test_citable_requires_actual_evidence(corpus):
    """The invariant Distinctive.__post_init__ enforces."""
    d = standout.Distinctive(
        kind="rare_flag",
        key="x",
        detail="d",
        comparison="c",
        direction="unique",
        strength=1.0,
        citable=True,
        evidence_event_ids=(),
    )
    assert d.citable is False


def test_broad_and_sparse_do_not_get_the_same_findings(broad, sparse):
    a = {(d.kind, d.key) for d in standout.distinctives(broad, NOW)}
    b = {(d.kind, d.key) for d in standout.distinctives(sparse, NOW)}
    assert a != b
    assert a, "the company with the broadest footprint has no findings at all"


def test_findings_are_not_identical_across_the_corpus(corpus):
    """THE DISCRIMINATION TEST.

    A summariser that says the same thing about everybody is the failure this module
    was written against — a confident 1.0 with no discrimination, a rule that fires for
    nobody, `axis_spreads` identically 0.0. So: no single finding may appear on every
    row, and no two rows may carry the identical finding set.
    """
    fr = standout.frame(NOW, refresh=True)
    sets = {f.company_id: {(d.kind, d.key) for d in standout.distinctives(f.company_id, NOW, fr=fr)} for f in fr.companies}

    everywhere = set.intersection(*sets.values()) if sets else set()
    assert not everywhere, f"these findings fired for EVERY company, so they discriminate nothing: {everywhere}"

    seen: dict[frozenset, str] = {}
    for cid, keys in sets.items():
        assert frozenset(keys) not in seen, f"{cid} and {seen[frozenset(keys)]} have identical findings"
        seen[frozenset(keys)] = cid


def test_a_universal_integrity_flag_is_not_a_finding(corpus, monkeypatch):
    """`date_inferred` sits on essentially every ingested event on the real corpus.
    Reporting it as distinctive is the 13-of-13 failure this bar exists to catch."""
    fr = standout.frame(NOW, refresh=True)
    universal = standout.Frame(
        as_of=NOW,
        companies=tuple(
            standout.Features(**{**f.__dict__, "integrity_flags": ("date_inferred",)})
            for f in fr.companies
        ),
    )
    me = universal.companies[0]
    assert not [d for d in standout._integrity_distinctives(me, universal) if d.kind == "integrity_flag"]


# ---------------------------------------------------------------------------
# 2. THE SPARSE COMPANY. The case that decides whether this feature is honest.
# ---------------------------------------------------------------------------


def test_sparse_company_manufactures_nothing(sparse, monkeypatch):
    """Deck claims only. The honest card says so and costs zero model calls."""

    def _boom(*a, **k):
        raise AssertionError("a company with no citable evidence must not reach the model")

    monkeypatch.setattr("core.llm.complete", _boom)

    out = standout.generate(sparse, NOW, refresh=True)

    assert out["summary_source"] == "computed"
    assert out["citations"] == []
    assert "no independent public artifact was found" in out["summary"]
    kinds = {d["kind"] for d in out["distinctives"]}
    assert "no_public_artifact" in kinds
    # Nothing on this card may be presented as a positive distinction.
    assert not [d for d in out["distinctives"] if d["kind"] in ("rare_flag", "evidence_density") and d["direction"] == "above"]


def test_sparse_company_absence_outranks_everything(sparse):
    out = standout.generate(sparse, NOW, refresh=True)
    assert out["distinctives"][0]["kind"] == "no_public_artifact"


def test_absence_findings_are_never_citable(corpus):
    fr = standout.frame(NOW, refresh=True)
    for f in fr.companies:
        for d in standout.distinctives(f.company_id, NOW, fr=fr):
            if d.direction == "absent" or d.kind in ("missing_flag", "integrity_flag", "gate_divergence"):
                assert not d.citable, f"{d.kind}/{d.key} claims to be citable but is an absence"


def test_empty_findings_produce_an_honest_sentence():
    text = standout._computed_prose([], "Nobody Corp")
    assert "Nothing about Nobody Corp" in text
    assert "separates it" in text


# ---------------------------------------------------------------------------
# 3. ANTI-HALLUCINATION. Non-negotiable.
# ---------------------------------------------------------------------------


def _capture(monkeypatch, response):
    """Mock llm.complete, recording every string it is handed."""
    seen: list[dict] = []

    def fake(prompt, *, system=None, tier="fast", untrusted=None, json_mode=False, temperature=0.2):
        seen.append({"prompt": prompt, "system": system, "untrusted": untrusted})
        return response

    monkeypatch.setattr("core.llm.complete", fake)
    return seen


def test_no_url_reaches_the_model(broad, monkeypatch):
    """THE mechanism. Every event in this corpus has a real source_url; not one of
    them, nor anything URL-shaped, may appear in anything handed to llm.complete."""
    seen = _capture(monkeypatch, {"sentences": []})
    standout.generate(broad, NOW, refresh=True)

    assert seen, "the broad-footprint company should have reached the model"
    for call in seen:
        for label, text in call.items():
            if not text:
                continue
            hit = standout._URLISH.search(text)
            assert hit is None, f"{label} contains URL-shaped text: {hit.group(0)!r}"
            assert "github.com" not in text
            assert "https://" not in text


def test_the_model_sees_opaque_ids_and_code_resolves_the_urls(broad, monkeypatch):
    seen = _capture(
        monkeypatch,
        {"sentences": [{"text": "The commit is part of pagekv work on refcounted physical pages across 9 distinct month(s).", "ref": "e1"}]},
    )
    out = standout.generate(broad, NOW, refresh=True)

    assert "e1:" in seen[0]["prompt"], "the evidence index must key on opaque ids"
    assert "[e1]" in (seen[0]["untrusted"] or ""), "spans must be keyed by opaque id"
    # The URL exists in the output, and it came from the store, not the model.
    assert out["citations"]
    for c in out["citations"]:
        assert c["source_url"].startswith("https://github.com/")
        assert c["ref_id"].startswith("e")
        assert c["evidence_span"]


def test_spans_go_through_the_untrusted_channel_and_are_not_duplicated(broad, monkeypatch):
    """Duplicating a span into the prompt defeats the injection wrapper — the exact
    failure api/memo.py's `_citable` exists to prevent."""
    seen = _capture(monkeypatch, {"sentences": []})
    standout.generate(broad, NOW, refresh=True)
    assert "refcounted physical pages" in (seen[0]["untrusted"] or "")
    assert "refcounted physical pages" not in seen[0]["prompt"]


def test_invented_ref_is_dropped(broad, monkeypatch):
    _capture(monkeypatch, {"sentences": [{"text": "Something happened here.", "ref": "e99"}]})
    out = standout.generate(broad, NOW, refresh=True)
    assert out["summary_source"] == "computed"
    assert out["citations"] == []
    assert any("e99" in d for d in out["dropped_sentences"])


def test_ungrounded_sentence_is_dropped_not_shown(broad, monkeypatch):
    """An assertion that cannot be resolved is DELETED. It is never shown with a caveat."""
    _capture(
        monkeypatch,
        {"sentences": [{"text": "They shipped a Kubernetes autoscaler for Snowflake.", "ref": "e1"}]},
    )
    out = standout.generate(broad, NOW, refresh=True)
    assert "Kubernetes" not in out["summary"]
    assert "Snowflake" not in out["summary"]
    assert out["dropped_sentences"]


def test_a_url_in_model_output_is_dropped(broad, monkeypatch):
    _capture(
        monkeypatch,
        {"sentences": [{"text": "See github.com/broad/core for the commits.", "ref": "e1"}]},
    )
    out = standout.generate(broad, NOW, refresh=True)
    assert "github.com" not in out["summary"]
    assert any("URL-shaped" in d for d in out["dropped_sentences"])


def test_one_bad_sentence_does_not_void_a_good_one(broad, monkeypatch):
    _capture(
        monkeypatch,
        {
            "sentences": [
                {"text": "The commit is part of pagekv work on refcounted physical pages across 9 distinct month(s).", "ref": "e1"},
                {"text": "Their Kubernetes rollout was flawless.", "ref": "e1"},
            ]
        },
    )
    out = standout.generate(broad, NOW, refresh=True)
    assert "refcounted" in out["summary"]
    assert "Kubernetes" not in out["summary"]
    assert len(out["dropped_sentences"]) == 1


def test_a_sentence_without_the_comparison_is_dropped(broad, monkeypatch):
    """"The rule fired for this company" is true, grounded, and pasteable onto any row
    that fired it. Without the figure it is a description, not a finding."""
    _capture(
        monkeypatch,
        {"sentences": [{"text": "The commit is part of pagekv work on refcounted physical pages.", "ref": "e1"}]},
    )
    out = standout.generate(broad, NOW, refresh=True)
    assert out["summary_source"] == "computed"
    assert any("no figure" in d for d in out["dropped_sentences"])
    # The computed fallback always carries the comparison the model dropped.
    assert "median" in out["summary"]


def test_model_outage_still_produces_a_card(broad, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no credits")

    monkeypatch.setattr("core.llm.complete", boom)
    out = standout.generate(broad, NOW, refresh=True)
    assert out["summary"].strip()
    assert out["summary_source"] == "computed"
    assert out["distinctives"]


def test_every_citation_resolves_to_a_stored_event_with_a_span(broad, monkeypatch):
    _capture(
        monkeypatch,
        {"sentences": [{"text": "The commit is part of pagekv work on refcounted physical pages across 9 distinct month(s).", "ref": "e1"}]},
    )
    from memory import store
    from api.routers.deps import as_uuid

    out = standout.generate(broad, NOW, refresh=True)
    assert out["citations"]
    for c in out["citations"]:
        ev = store.get_event(as_uuid(c["event_id"]))
        assert ev is not None
        assert (ev.evidence_span or "").strip()
        assert ev.source_url == c["source_url"]


# ---------------------------------------------------------------------------
# 4. CACHING and the list contract.
# ---------------------------------------------------------------------------


def test_second_call_does_not_hit_the_model(broad, monkeypatch):
    seen = _capture(
        monkeypatch,
        {"sentences": [{"text": "The commit is part of pagekv work on refcounted physical pages across 9 distinct month(s).", "ref": "e1"}]},
    )
    first = standout.generate(broad, NOW, refresh=True)
    second = standout.generate(broad, NOW)
    assert len(seen) == 1
    assert second["cached"] is True
    assert second["summary"] == first["summary"]


def test_new_evidence_invalidates_the_cache(broad, monkeypatch, corpus):
    from memory import store

    _capture(monkeypatch, {"sentences": []})
    standout.generate(broad, NOW, refresh=True)
    fr = standout.frame(NOW)
    before = standout.cache_key(str(broad), NOW, fr)

    store.append(
        _event(
            broad,
            uuid4(),
            EventKind.RELEASE,
            "github",
            days_ago=1,
            span='release v2.1.0 "wal: fsync batching"',
            url="https://github.com/broad/core/releases/v2.1.0",
        )
    )
    after = standout.cache_key(str(broad), NOW, standout.frame(NOW, refresh=True))
    assert before != after


def test_a_change_on_ANOTHER_company_invalidates_this_one(broad, corpus, monkeypatch):
    """Distinctiveness is comparative, so the corpus is part of the key. Without this,
    a card keeps asserting 'rare' after the thing stopped being rare."""
    from memory import store

    _capture(monkeypatch, {"sentences": []})
    standout.generate(broad, NOW, refresh=True)
    before = standout.cache_key(str(broad), NOW, standout.frame(NOW))

    other = corpus[1]["company_id"]
    for i in range(12):
        store.append(
            _event(
                other,
                uuid4(),
                EventKind.REPO_ACTIVITY,
                "github",
                days_ago=15 * i + 2,
                span=f'commit ffee{i}1 "index: bloom filter"',
                url=f"https://github.com/middle/core/commit/ffee{i}1",
            )
        )
    after = standout.cache_key(str(broad), NOW, standout.frame(NOW, refresh=True))
    assert before != after, "a corpus change must invalidate every card computed against it"


def test_cached_never_computes_on_a_cold_frame(broad, monkeypatch):
    standout.reset_cache()

    def boom(*a, **k):
        raise AssertionError("cached() must not build the frame")

    monkeypatch.setattr(standout, "_in_scope_companies", boom)
    assert standout.cached(broad, NOW) is None


def test_not_generated_is_an_explicit_marker_never_an_empty_string(broad):
    marker = standout.not_generated(broad)
    assert marker["status"] == "not_generated"
    assert marker["summary"] is None
    assert marker["summary"] != ""


def test_ranked_row_carries_the_marker_when_nothing_is_cached(corpus, monkeypatch):
    from api.main import _standout_for

    standout.reset_cache()
    out = _standout_for(corpus[0]["company_id"], NOW)
    assert out["status"] == "not_generated"
    assert out["summary"] is None


def test_ranked_row_serves_the_summary_once_it_is_cached(broad, monkeypatch):
    _capture(
        monkeypatch,
        {"sentences": [{"text": "The commit is part of pagekv work on refcounted physical pages across 9 distinct month(s).", "ref": "e1"}]},
    )
    from api.main import _standout_for

    standout.generate(broad, NOW, refresh=True)
    out = _standout_for(broad, NOW)
    assert out.get("status") != "not_generated"
    assert "refcounted" in out["summary"]
    assert out["citations"]


def test_payload_is_json_serialisable(broad, monkeypatch):
    _capture(monkeypatch, {"sentences": []})
    out = standout.generate(broad, NOW, refresh=True)
    json.dumps(out, default=str)
    assert set(out) >= {"summary", "summary_source", "citations", "distinctives", "dropped_sentences"}
