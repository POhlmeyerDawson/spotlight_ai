"""The system must work for ANY industry the fund names, not just software.

The failure this file pins down is not that a non-software founder scored LOW. It
is that they scored UNINFORMATIVE — no source in the registry could see them, so no
rule was applicable, so y_t came back at the prior — and under
`min_axis_with_momentum_tiebreak` that lands in the same place as being weak. "We
have no signal on this person" and "this person is weak" must not produce the same
rank, and every test below exists to hold one half of that apart from the other.

Two things are asserted throughout and neither is negotiable:

  ABSENCE STAYS UNKNOWN. A founder with no reachable channel has the sector rules
      SKIPPED, not failed. They never enter the denominator of y_t. This is the same
      None-not-0.0 discipline `data/traits.json` applies and it is the property that
      makes the whole design safe to ship.

  THE AI-INFRA PATH IS FROZEN. The shipped thesis must resolve to exactly the rule
      set and the allowlist it always did, by identity rather than by coincidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from core import search as web_search
from intelligence import flags
from schema.events import Event, EventKind, Source

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=400)


# ---------------------------------------------------------------------------
# Thesis fixtures. A thesis is config, so pointing the reader at a tmp dir is the
# whole mechanism — nothing else needs to change for a fund to change industry.
# ---------------------------------------------------------------------------


def _write_thesis(tmp_path: Path, monkeypatch, *sector_ids: str) -> Path:
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / "thesis.json").write_text(
        json.dumps(
            {
                "name": "test thesis",
                "sectors": [
                    {"id": s, "label": s.replace("-", " "), "include": True} for s in sector_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VCBRAIN_SEED_DIR", str(seed_dir))
    return seed_dir


@pytest.fixture
def thesis(tmp_path, monkeypatch):
    def _set(*sector_ids: str) -> None:
        _write_thesis(tmp_path, monkeypatch, *sector_ids)

    return _set


def _event(
    entity_id,
    *,
    kind: EventKind = EventKind.REPO_ACTIVITY,
    source: Source = Source.WEB,
    url: str,
    text: str,
    day: int = 0,
    source_id: str | None = None,
) -> Event:
    payload = {"text": text}
    if source_id:
        payload["source_id"] = source_id
    return Event(
        entity_id=entity_id,
        kind=kind,
        source=source,
        source_url=url,
        observed_at=T0 + timedelta(days=day),
        payload=payload,
        evidence_span=text,
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# 1. The AI-infra path is frozen
# ---------------------------------------------------------------------------


def test_shipped_thesis_rule_set_is_RULES_by_identity() -> None:
    """Not a superset, not an equal copy — the same object.

    Identity is the assertion because it is the only form that cannot drift. If a
    future evidence class is tagged for a software sector this fails loudly, which
    is the point: widening the demo path should be a decision somebody makes on
    purpose, not something that happens while adding an industry.
    """
    assert flags._active_rules() is flags.RULES


def test_shipped_thesis_allowlist_is_unchanged() -> None:
    """The 42 domains the registry always resolved to, still 42, still the same."""
    domains = web_search.allowed_domains()
    assert len(domains) == 42
    assert "github.com" in domains
    # None of the sector sources this workstream added may leak into the demo path.
    for added in ("clinicaltrials.gov", "sec.gov", "patents.google.com", "hackaday.io"):
        assert added not in domains


# ---------------------------------------------------------------------------
# 2. Changing the thesis changes behaviour, measurably
# ---------------------------------------------------------------------------


def test_a_different_thesis_resolves_a_different_allowlist(thesis) -> None:
    thesis("ai-infra", "dev-tools")
    software = set(web_search.allowed_domains())

    thesis("fintech")
    fintech = set(web_search.allowed_domains())

    thesis("biotech")
    biotech = set(web_search.allowed_domains())

    assert software != fintech != biotech
    # Each thesis reaches its own primary record, and cannot reach the others'.
    assert {"sec.gov", "fca.org.uk"} <= fintech
    assert "sec.gov" not in software and "sec.gov" not in biotech
    assert {"clinicaltrials.gov", "pubmed.ncbi.nlm.nih.gov"} <= biotech
    assert "clinicaltrials.gov" not in software and "clinicaltrials.gov" not in fintech
    # github is a software source and stays one; a fintech fund does not search it.
    assert "github.com" in software and "github.com" not in fintech


def test_a_different_thesis_activates_different_rules(thesis) -> None:
    thesis("ai-infra")
    software_rules = {r.rule_id for r in flags._active_rules()}

    thesis("fintech")
    fintech_rules = {r.rule_id for r in flags._active_rules()}

    thesis("biotech")
    biotech_rules = {r.rule_id for r in flags._active_rules()}

    assert software_rules < fintech_rules  # strict superset: the base rules always apply
    assert "regulatory_licence_held" in fintech_rules
    assert "regulatory_licence_held" not in software_rules
    assert "clinical_trial_registered" in biotech_rules
    assert "clinical_trial_registered" not in fintech_rules


def test_the_registry_reports_where_it_cannot_see(thesis) -> None:
    """Coverage is a product output, not a comment in a config file."""
    thesis("biotech")
    assert web_search.registry_coverage()["thinly_covered_sectors"] == []

    thesis("design")
    thin = web_search.registry_coverage()
    assert thin["thinly_covered_sectors"] == ["design"]
    assert thin["caveat"] and "could not see them" in thin["caveat"]


# ---------------------------------------------------------------------------
# 3. The load-bearing one: absence stays UNKNOWN
# ---------------------------------------------------------------------------


def test_a_fintech_founder_with_no_filing_is_not_scored_against_one(thesis) -> None:
    """The whole task, in one assertion.

    A fintech founder we have no financial-record evidence for must have the
    fintech rules SKIPPED — never evaluated-and-unfired. Evaluated-and-unfired
    would put them in the denominator of y_t, which is the "missing = zero" failure
    that sinks precisely the founders this product exists to find.
    """
    thesis("fintech")
    entity_id = uuid4()
    only_a_blog = [
        _event(entity_id, url="https://substack.com/p/why-we-are-building", text="a post")
    ]
    fired = flags.evaluate_events(only_a_blog, entity_id=entity_id, as_of=AS_OF)
    evaluated = {e.payload["rule_id"] for e in fired}
    assert "regulatory_licence_held" not in evaluated
    assert "sustained_sector_record_90d" not in evaluated


def test_a_fintech_founder_with_a_licence_is_actually_readable(thesis) -> None:
    """And the other half: real evidence in a real channel produces a real reading.

    Without this the sector rules could be inert and every absence test above would
    still pass — which is exactly the shape of failure this codebase has shipped
    before (a metric returning 1.0 with no discrimination).
    """
    thesis("fintech")
    entity_id = uuid4()
    events = [
        _event(
            entity_id,
            url="https://www.fca.org.uk/firms/financial-services-register",
            text="Authorised payment institution, firm reference number 000000, "
            "licence granted",
            day=0,
            source_id="financial_filings",
        ),
        _event(
            entity_id,
            url="https://www.sec.gov/edgar/browse/",
            text="Form ADV filed by the adviser",
            day=200,
            source_id="financial_filings",
        ),
        _event(
            entity_id,
            url="https://www.federalregister.gov/documents/",
            text="comment submitted on the proposed rule",
            day=300,
            source_id="financial_filings",
        ),
    ]
    fired = flags.evaluate_events(events, entity_id=entity_id, as_of=AS_OF)
    by_rule = {e.payload["rule_id"]: e.payload["fired"] for e in fired}

    assert by_rule.get("regulatory_licence_held") is True
    assert by_rule.get("sustained_sector_record_90d") is True  # 300 days apart
    assert by_rule.get("revisited_sector_artifact") is True  # three touches

    y, r = flags.observation(fired)
    assert y > 0.0, "a licensed, filing, three-times-returning founder read as zero"
    assert (y, r) != flags.UNINFORMATIVE

    # Every fired rule cites the evidence it fired on. No receipt, no rule.
    for event in fired:
        if event.payload["fired"]:
            assert event.payload["evidence_event_ids"]
            assert event.observed_at <= AS_OF  # Invariant #1


def test_the_same_founder_is_invisible_under_the_software_thesis(thesis) -> None:
    """The proof that the thesis is what changed, not the founder.

    Identical events, two theses. Under fintech the licence is readable evidence;
    under AI-infra the fintech rules do not exist, so the same person reads as
    someone who failed a pile of software questions nobody should have asked them.
    That asymmetry is the entire deliverable, and it also shows the reading comes
    from the sector configuration rather than from something incidental in the text.

    Note what this test found and what was fixed because of it. The first version
    asserted the AI-infra reading would be UNINFORMATIVE. It is not: the founder's
    web event makes the source-agnostic keyword rules applicable, and they all fail,
    so the reading is a CONFIDENT ZERO — the exact "structurally unreadable lands at
    the bottom anyway" pathology, arriving by a route nobody had looked at. Two of
    those rules (`infra_domain_depth`, `benchmarks_published`) match software
    vocabulary and nothing else, and they are now skipped outside a software thesis.
    The rest ask sector-neutral questions about reasoning and legitimately stay.
    """
    entity_id = uuid4()
    events = [
        _event(
            entity_id,
            url="https://www.fca.org.uk/firms/financial-services-register",
            text="Authorised payment institution, firm reference number 000000",
            source_id="financial_filings",
        ),
    ]

    thesis("fintech")
    readable = flags.evaluate_events(events, entity_id=entity_id, as_of=AS_OF)
    y_fintech, _ = flags.observation(readable)

    thesis("ai-infra", "dev-tools")
    invisible = flags.evaluate_events(events, entity_id=entity_id, as_of=AS_OF)
    y_software, _ = flags.observation(invisible)

    assert {e.payload["rule_id"] for e in readable if e.payload["fired"]}
    assert not {e.payload["rule_id"] for e in invisible if e.payload["fired"]}
    assert y_fintech > y_software == 0.0

    # The software-vocabulary rules are asked of a software thesis and nobody else.
    sector_literal = {"infra_domain_depth", "benchmarks_published"}
    assert sector_literal <= {e.payload["rule_id"] for e in invisible}
    assert not (sector_literal & {e.payload["rule_id"] for e in readable})


def test_weight_parity_with_the_software_analogue() -> None:
    """A registered trial is worth what a shipped release is worth.

    Weighting the non-software instance lower would re-import the bias the
    equivalence table exists to remove, one decimal place at a time.
    """
    shipped = next(c for c in web_search.evidence_classes() if c["id"] == "shipped")
    release_weight = next(r.weight for r in flags.RULES if r.rule_id == "shipped_release")
    trial = next(i for i in shipped["instances"] if i["id"] == "clinical_trial_registered")
    assert trial["weight"] == release_weight


def test_every_evidence_class_names_a_trait_that_exists() -> None:
    """The equivalence table is config, so its cross-references must be checked.

    NOTE, and it is a real gap rather than a passing remark: `intelligence/traits.py`
    does not yet READ this field, so a sector rule that fires contributes to y_t but
    is not attributed to a trait in the TraitProfile. The link is declared and
    verified here; consuming it is follow-up work.
    """
    taxonomy = json.loads((ROOT / "data" / "traits.json").read_text(encoding="utf-8"))
    known = {t["id"] for t in taxonomy["traits"]}
    for cls in web_search.evidence_classes():
        assert cls["trait"] in known, f"{cls['id']} names an unknown trait {cls['trait']!r}"


def test_no_evidence_class_instance_collides_with_an_existing_rule_id() -> None:
    existing = {r.rule_id for r in flags.RULES} | {r.rule_id for r in flags.CAREER_HISTORY_RULES}
    for cls in web_search.evidence_classes():
        for instance in cls["instances"]:
            assert instance["id"] not in existing


# ---------------------------------------------------------------------------
# 4. The source penalty: self-published down, independently corroborated up
# ---------------------------------------------------------------------------


def test_corroboration_is_priced_and_is_neutral_when_unstated() -> None:
    """`web 1.0 / deck 2.0` encoded "code is trustworthy". This is the replacement.

    It is orthogonal to code: what moves the multiplier is how many channels stand
    behind the reading that the founder does not control.
    """
    from memory import score

    assert score._corroboration_multiplier({}) == 1.0  # nothing predating this moves
    assert score._corroboration_multiplier({"independent_channels": 1}) == 1.0
    corroborated = score._corroboration_multiplier({"independent_channels": 3})
    solo = score._corroboration_multiplier(
        {"independent_channels": 0, "self_published_only": True}
    )
    assert corroborated < 1.0 < solo


def test_a_self_published_only_dossier_is_marked_as_such() -> None:
    """A founder we only have their own word for. Not weak — unwitnessed."""
    entity_id = uuid4()
    own_words = [
        _event(
            entity_id,
            kind=EventKind.DECK_CLAIM,
            source=Source.DECK,
            url="https://medium.com/@founder/our-approach",
            text="we serve 10k requests per second",
        )
    ]
    reading = flags._corroboration_reading(own_words)
    assert reading["independent_channels"] == 0
    assert reading["self_published_only"] is True


def test_a_third_party_channel_counts_as_a_witness() -> None:
    entity_id = uuid4()
    events = [
        _event(entity_id, source=Source.DECK, url="https://x.com/founder", text="we shipped"),
        _event(
            entity_id,
            source=Source.GITHUB,
            url="https://github.com/acme/thing/releases/tag/v1.0.0",
            text="v1.0.0",
        ),
        _event(
            entity_id,
            source=Source.HN,
            url="https://news.ycombinator.com/item?id=1",
            text="Show HN",
        ),
    ]
    reading = flags._corroboration_reading(events)
    assert reading["independent_channels"] == 2
    assert reading["self_published_only"] is False
    # The founder's own surfaces are never witnesses to the founder.
    assert "Source.DECK" not in reading["independent_channel_ids"]


# ---------------------------------------------------------------------------
# 5. Invariant #3 still holds where it is most tempting to break it
# ---------------------------------------------------------------------------


def test_no_sector_source_scores_an_affiliation_or_a_funding_event() -> None:
    """New industries make affiliation MORE tempting, so it stays banned.

    A patent's assignee, a trial's sponsoring institution and a Form D are all
    organisation-shaped facts sitting in the new sources, and every one of them
    would reconstruct exactly what Invariant #3 forbids. The registry says so in
    prose; this asserts no rule can act on it.
    """
    banned_in_a_match = ("assignee", "sponsor", "affiliation", "form d", "funding", "raised")
    for cls in web_search.evidence_classes():
        for instance in cls["instances"]:
            pattern = str(instance.get("match") or "").lower()
            for term in banned_in_a_match:
                assert term not in pattern, (
                    f"{instance['id']} matches on {term!r} — that is an affiliation or "
                    f"funding proxy and SHARED.md Invariant #3 bars it from scoring."
                )
