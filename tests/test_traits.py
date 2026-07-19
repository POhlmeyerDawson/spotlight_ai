"""Trait taxonomy: mapping completeness, collapse identity, and DISCRIMINATION.

The discrimination tests are the point of this file. This codebase has repeatedly
produced code that looks implemented and measures nothing — a metric returning a
confident 1.0 for everyone, a substance rule reading payload keys that did not exist
and firing for nobody. A taxonomy that gives every founder the same trait profile is
decorative, so it is asserted here against real fixture corpora that the profiles
differ in the directions the archetypes are supposed to represent.

Fully offline: events are built from the seed fixtures, never read from a database.
"""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid5

import pytest

import scripts.seed as seed
from intelligence import flags, traits

VISIBLE_BUILDER = "vb-tensorpage"
COLD_START = "cs-veritanode"
INTERNATIONAL = "intl-zaryad"
ADVERSARIAL = "adv-synthgrid"
FAST_CONTROL = "adv-control-ferrite"
COHORT = (VISIBLE_BUILDER, COLD_START, INTERNATIONAL, ADVERSARIAL, FAST_CONTROL)


@pytest.fixture(scope="module")
def fixture_profiles() -> dict:
    out = {}
    for path in seed.fixture_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for profile in payload["profiles"]:
            out[profile["company_id"]] = (profile, payload["archetype"])
    return out


def _local_ids(profile: dict) -> dict:
    """Deterministic ids WITHOUT touching the store.

    seed.resolve_ids upserts into the event store, which dials the configured
    database. These tests must stay offline, and nothing here depends on the ids
    matching the ones a real seed run produces — only on them being stable and
    consistent within one profile.
    """
    keys = [profile["company_id"], *(f["key"] for f in profile["founders"])]
    keys += [prior["company_id"] for prior in profile.get("prior_companies", [])]
    return {key: uuid5(seed.NAMESPACE, f"test-traits|{key}") for key in keys}


@pytest.fixture(scope="module")
def cohort(fixture_profiles) -> dict:
    """slug -> (entity_id, events, as_of). Dates unshifted: the corpus is the claim."""
    built = {}
    for slug in COHORT:
        profile, _archetype = fixture_profiles[slug]
        ids = _local_ids(profile)
        entity_id = ids[profile["founders"][0]["key"]]
        events = [event for _, event in seed.build_events(profile, ids, timedelta(0))]
        as_of = max(event.observed_at for event in events) + timedelta(days=1)
        built[slug] = (entity_id, events, as_of)
    return built


@pytest.fixture(scope="module")
def profiles(cohort) -> dict:
    return {
        slug: traits.profile(entity_id, as_of, events=events)
        for slug, (entity_id, events, as_of) in cohort.items()
    }


# --- 1. mapping completeness -------------------------------------------------------


def test_every_flag_rule_is_mapped_or_explicitly_unmapped():
    """A rule that maps to nothing must be DECLARED, never silently dropped."""
    mapped = set(traits.rule_to_trait())
    declared_unmapped = set(traits.unmapped_rule_ids())
    all_rules = {rule.rule_id for rule in flags.RULES}
    missing = all_rules - mapped - declared_unmapped
    assert not missing, f"rules mapped to no trait and not declared unmapped: {sorted(missing)}"


def test_taxonomy_references_no_rule_that_does_not_exist():
    all_rules = {rule.rule_id for rule in flags.RULES}
    referenced = set(traits.rule_to_trait()) | set(traits.unmapped_rule_ids())
    assert not referenced - all_rules


def test_no_rule_is_mapped_to_two_traits():
    seen: dict[str, str] = {}
    for trait in traits.taxonomy()["traits"]:
        for rule_id in trait["flag_rules"]:
            assert rule_id not in seen, f"{rule_id} in both {seen.get(rule_id)} and {trait['id']}"
            seen[rule_id] = trait["id"]


def test_proof_fast_start_is_the_declared_unmapped_rule():
    """Reaction latency is not a trait. See data/traits.json unmapped_rules.

    SOURCES.md already bans off-hours commit activity as a signal because it proxies
    for having no caregiving responsibilities; a 30-minute response window is the same
    proxy at a shorter timescale. If somebody later invents a trait to house this rule,
    this test should fail and the argument should be had again.
    """
    assert traits.unmapped_rule_ids() == ("proof_fast_start",)


def test_every_trait_declares_its_absence_semantics():
    for trait in traits.taxonomy()["traits"]:
        assert trait["absence"] in {"MEANINGFUL", "UNKNOWN", "CONDITIONAL"}
        if trait["absence"] == "CONDITIONAL":
            assert trait["absence_predicate"].strip(), f"{trait['id']} CONDITIONAL with no predicate"
        assert trait["gaming"].strip() and trait["gaming_cost"].strip()
        assert trait["sources"], f"{trait['id']} claims no source can evidence it"


# --- 2. the bridge to the existing filter ------------------------------------------


@pytest.mark.parametrize("slug", COHORT)
def test_collapse_is_identical_to_the_existing_scalar(slug, cohort, profiles):
    """The taxonomy must not move a single band.

    Trait weight = applicable rule-weight mass, so the weighted trait mean is
    algebraically the weighted YES-rate flags.observation() already produces. If this
    ever fails, somebody has re-tuned the sensor while claiming to rename things, and
    every founder's mu and band moved with no calibration run behind it.
    """
    entity_id, events, as_of = cohort[slug]
    y_t, _ = flags.observation(flags.evaluate_events(events, entity_id, as_of))
    assert profiles[slug].scalar == pytest.approx(y_t, abs=1e-12)


def test_collapse_of_nothing_is_uninformative(cohort):
    """No applicable rules must tell the filter NOTHING, not zero."""
    entity_id, _, as_of = cohort[COLD_START]
    profile = traits.profile(entity_id, as_of, events=[])
    assert profile.scalar == flags.UNINFORMATIVE[0]
    assert all(not trait.applicable_rules for trait in profile.traits.values())


def test_corroboration_never_reaches_the_score(cohort, profiles):
    """Corroboration multiplies CONFIDENCE only. If it touched the score it would
    leak into the scalar and destabilise the band."""
    for slug in COHORT:
        for trait in profiles[slug].traits.values():
            mass = trait.weight_mass
            if mass <= 0:
                continue
            fired_mass = sum(
                rule.weight for rule in flags.RULES if rule.rule_id in trait.fired_rules
            )
            assert trait.score == pytest.approx(fired_mass / mass)


# --- 3. triangulation --------------------------------------------------------------


def test_two_signals_from_the_same_source_are_one_channel(profiles):
    """GitHub commits and GitHub releases corroborate each other far less than a
    GitHub release and an HN thread do. Channels are DISTINCT sources."""
    velocity = profiles[VISIBLE_BUILDER].traits["iteration_velocity"]
    assert len(velocity.fired_rules) >= 5  # many github rules...
    assert velocity.channels == ("github",)  # ...but one channel
    assert velocity.corroboration == traits.corroboration(1)


def test_corroboration_rises_with_independent_channels_and_saturates():
    assert traits.corroboration(0) == 0.0
    assert traits.corroboration(1) < traits.corroboration(2) < traits.corroboration(3)
    assert traits.corroboration(3) == traits.corroboration(9) == 1.0


def test_min_channels_gates_the_cheap_to_game_traits(profiles):
    """problem_scoping and learns_from_failure are keyword matches over text the
    founder wrote. One channel of that is a claim, not an observation."""
    for trait_id in ("technical_depth", "problem_scoping", "learns_from_failure"):
        assert traits._trait_def(trait_id)["min_channels"] == 2
    scoping = profiles[ADVERSARIAL].traits["problem_scoping"]
    assert scoping.fired_rules
    assert not scoping.observed  # evidenced, but not corroborated -> not observed


def test_self_attested_channels_do_not_corroborate(profiles):
    """A deck is founder-authored prose with no third party in the loop.

    The Type 5 adversary cleared the two-channel gate on problem_scoping in the live
    corpus using deck + GitHub, because the same keyword stuffing appears in the pitch
    and in the repo description. Both are surfaces the founder controls unilaterally,
    so counting them as two witnesses counts one person twice.
    """
    assert "deck" in traits.self_attested_channels()
    assert traits.independent_channels(("deck", "github")) == ("github",)
    assert traits.independent_channels(("deck",)) == ()
    scoping = profiles[ADVERSARIAL].traits["problem_scoping"]
    assert len(traits.independent_channels(scoping.channels)) < scoping.min_channels


# --- 4. per-source attribution -----------------------------------------------------


def test_attribution_is_computed_not_asserted(cohort, profiles):
    """Each (source, trait) delta must equal the real leave-one-source-out marginal."""
    entity_id, events, as_of = cohort[VISIBLE_BUILDER]
    for contribution in profiles[VISIBLE_BUILDER].attribution:
        if contribution.sole_channel:
            continue
        remainder = [e for e in events if str(e.source) != contribution.source]
        without = traits.profile(entity_id, as_of, events=remainder, attribute=False)
        expected = (
            profiles[VISIBLE_BUILDER].traits[contribution.trait_id].score
            - without.traits[contribution.trait_id].score
        ) * 100.0
        assert contribution.delta_points == pytest.approx(round(expected, 1))


def test_attribution_can_be_negative(profiles):
    """A source that only ever adds is a source we are not reading honestly.

    Looking at GitHub genuinely LOWERS Tensorpage's ships_to_users read: it brings
    external_contributors into scope and that rule does not fire. SOURCES.md §4 asks
    for why each source adds OR SUBTRACTS; this is the subtract case, and the UI must
    be able to show it.
    """
    github_bars = [
        c
        for c in profiles[VISIBLE_BUILDER].attribution
        if c.source == "github" and not c.sole_channel
    ]
    assert any(c.delta_points < 0 for c in github_bars)


def test_sole_channel_is_flagged_not_rendered_as_a_marginal(profiles):
    """Single-channel fragility must not masquerade as a huge source effect."""
    sole = [c for c in profiles[INTERNATIONAL].attribution if c.sole_channel]
    assert sole, "expected github to be the sole channel for several Zaryad traits"
    assert {c.trait_id for c in sole} >= {"iteration_velocity", "engineering_rigor"}


def test_attribution_cites_real_evidence_events(cohort, profiles):
    """Every bar must resolve to events that exist, so the UI can drill to a span."""
    for slug in COHORT:
        entity_id, events, as_of = cohort[slug]
        known = {str(event.event_id) for event in events}
        for contribution in profiles[slug].attribution:
            assert set(contribution.evidence_event_ids) <= known


def test_a_source_with_no_events_produces_no_bars(profiles):
    for slug in COHORT:
        present = set(profiles[slug].sources_present)
        assert {c.source for c in profiles[slug].attribution} <= present


# --- 5. DISCRIMINATION -------------------------------------------------------------


def test_adversary_outscores_the_control_on_the_keyword_trait_but_is_not_observed(profiles):
    """The Type 5 adversary genuinely WINS technical_depth on score.

    Keyword-stuffed deck copy beats a real builder on a regex, 0.43 to 0.00. That is
    a fact about the rules, not a bug this module can fix, and pretending otherwise
    is how a containment story ships as a comment that measures nothing. What must
    hold is that the corroboration gate refuses to call it OBSERVED: deck and GitHub
    are one person's unilateral surfaces, so they reduce to one independent channel
    against a min_channels of 2.
    """
    adversary, control = profiles[ADVERSARIAL], profiles[FAST_CONTROL]
    depth = adversary.traits["technical_depth"]
    assert depth.score > control.traits["technical_depth"].score
    assert depth.evidenced and not depth.observed
    assert len(traits.independent_channels(depth.channels)) < depth.min_channels
    # ...and the separation that actually matters survives it.
    assert control.scalar > adversary.scalar


def test_the_five_profiles_are_not_all_the_same(profiles):
    """If every founder gets the same trait profile the taxonomy is decorative."""
    vectors = {slug: tuple(profiles[slug].vector().values()) for slug in COHORT}
    assert len(set(vectors.values())) == len(COHORT), f"non-distinct trait profiles: {vectors}"


def test_cold_start_is_unknown_everywhere_rather_than_low(profiles):
    """Type 2. No public artifact must read as 'we cannot say', never as 'weak'."""
    cold = profiles[COLD_START]
    assert all(score is None for score in cold.vector().values())
    assert cold.scalar == flags.UNINFORMATIVE[0]
    assert cold.sources_present == ("deck",)


def test_visible_builder_beats_the_adversary_on_the_expensive_traits(profiles):
    """Type 1 vs Type 5. The separation must sit in traits that cost calendar time."""
    builder, adversary = profiles[VISIBLE_BUILDER], profiles[ADVERSARIAL]
    assert builder.traits["iteration_velocity"].score > adversary.traits["iteration_velocity"].score
    assert builder.traits["ships_to_users"].score > adversary.traits["ships_to_users"].score
    assert builder.scalar > adversary.scalar


def test_fast_builder_control_is_not_confused_with_the_adversary(profiles):
    """Type 5's whole point: an even LARGER burst with real substance must separate.

    Ferrite Labs and Synthgrid look identical to a volume metric. The trait that has
    to tell them apart is iteration_velocity, because burst_with_substance gates the
    volume on measured diff substance rather than commit count.
    """
    control, adversary = profiles[FAST_CONTROL], profiles[ADVERSARIAL]
    assert "burst_with_substance" in control.traits["iteration_velocity"].fired_rules
    assert "burst_with_substance" not in adversary.traits["iteration_velocity"].fired_rules
    assert control.traits["iteration_velocity"].score > adversary.traits["iteration_velocity"].score
    assert control.scalar > adversary.scalar


def test_international_founder_is_scored_on_traits_not_on_visibility(profiles):
    """Type 6. Zaryad's evidence carries transliterated_name on every event and its
    footprint is thinner than Tensorpage's. The traits that measure BUILDING must
    still come out comparable — otherwise the taxonomy has learned geography.
    """
    intl, builder = profiles[INTERNATIONAL], profiles[VISIBLE_BUILDER]
    assert intl.traits["iteration_velocity"].score == pytest.approx(
        builder.traits["iteration_velocity"].score
    )
    assert intl.traits["ships_to_users"].score > 0.0
    assert intl.scalar > profiles[ADVERSARIAL].scalar


def test_traits_discriminate_within_a_founder_not_just_between_them(profiles):
    """A profile where every trait scores the same is a scalar wearing seven hats."""
    for slug in (VISIBLE_BUILDER, INTERNATIONAL, ADVERSARIAL, FAST_CONTROL):
        scores = [s for s in profiles[slug].vector().values() if s is not None]
        assert len(set(round(s, 4) for s in scores)) > 1, f"{slug} has a flat profile"
