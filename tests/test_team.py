"""Team-level scoring: aggregation, uncertainty propagation, and the solo case.

These tests are store-free by construction — they drive `team.assemble` with explicit
`Member` values. The point is to pin the AGGREGATION contract, which is the part that
must not silently change. The corpus is currently 100% solo founders (see
`test_solo_is_a_lossless_passthrough`), so a store-only test suite would exercise the
single-member path and nothing else, and would have passed against an aggregation that
was completely wrong for two founders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from intelligence import team

T0 = datetime(2025, 6, 1, tzinfo=timezone.utc)
COMPANY = UUID("11111111-1111-1111-1111-111111111111")

ALL_TRAITS = (
    "engineering_rigor",
    "iteration_velocity",
    "learns_from_failure",
    "problem_scoping",
    "responds_to_scrutiny",
    "ships_to_users",
    "technical_depth",
)


def _member(
    mu: float,
    band: float = 0.1,
    trend: float = 0.0,
    observed: tuple[str, ...] = (),
    entity_id: UUID | None = None,
) -> team.Member:
    return team.Member(
        entity_id=entity_id or uuid4(),
        mu=mu,
        band=band,
        trend=trend,
        observed_traits=tuple(sorted(observed)),
        evidenced_traits=tuple(sorted(observed)),
        contributing_event_ids=(uuid4(),),
    )


# --- solo founders ------------------------------------------------------------------


def test_solo_is_a_lossless_passthrough_and_is_not_penalised() -> None:
    """A team of one must score exactly as that founder scores. No construction penalty."""
    member = _member(mu=0.73, band=0.08, trend=0.11, observed=("ships_to_users",))
    ts = team.assemble(COMPANY, T0, (member,))

    assert ts.mu == member.mu
    assert ts.band == member.band
    assert ts.trend == member.trend
    assert ts.dispersion == 0.0
    assert ts.complementarity_lift == 0.0
    assert ts.is_solo and ts.basis == "solo"


def test_solo_reports_complementarity_as_not_measured_never_as_zero() -> None:
    """0.0 would claim we measured complementarity and found none. None says we did not."""
    ts = team.assemble(COMPANY, T0, (_member(mu=0.6),))
    assert ts.complementarity is None
    assert "solo" in ts.complementarity_basis


def test_no_resolved_founder_is_its_own_state() -> None:
    ts = team.assemble(COMPANY, T0, ())
    assert ts.basis == "no_resolved_founder"
    assert ts.complementarity is None
    assert ts.band == team._PRIOR_SD


# --- aggregation ---------------------------------------------------------------------


def test_aggregation_is_not_a_mean() -> None:
    """The distinction the brief names: one strong solo vs two mediocre co-founders."""
    solo = team.assemble(COMPANY, T0, (_member(mu=0.80, observed=("ships_to_users",)),))
    pair = team.assemble(
        COMPANY,
        T0,
        (
            _member(mu=0.45, observed=("ships_to_users",)),
            _member(mu=0.45, observed=("technical_depth",)),
        ),
    )
    assert solo.mu > pair.mu, "a mean would have made these nearly indistinguishable"
    # And the number a mean WOULD have produced is still reported, so the choice is visible.
    assert pair.mean_mu == pytest.approx(0.45)
    assert pair.mu > pair.mean_mu


def test_a_weak_cofounder_never_drags_the_strong_one_down() -> None:
    strong = _member(mu=0.80, band=0.05, observed=("ships_to_users", "technical_depth"))
    weak = _member(mu=0.20, band=0.05, observed=("ships_to_users",))
    ts = team.assemble(COMPANY, T0, (strong, weak))
    assert ts.mu >= strong.mu
    assert ts.anchor_mu == strong.mu


def test_complementary_cofounder_lifts_more_than_a_redundant_one() -> None:
    """The whole claim of the module: coverage of NEW ground is what earns the lift."""
    anchor = _member(mu=0.70, observed=("ships_to_users", "iteration_velocity"))

    redundant = team.assemble(
        COMPANY, T0, (anchor, _member(mu=0.60, observed=("ships_to_users",)))
    )
    complementary = team.assemble(
        COMPANY, T0, (anchor, _member(mu=0.60, observed=("technical_depth", "problem_scoping")))
    )

    assert redundant.complementarity == 0.0, "duplicating the anchor adds no capability"
    assert complementary.complementarity > 0.0
    assert complementary.mu > redundant.mu


def test_coverage_alone_is_not_capability() -> None:
    """A co-founder standing in an empty part of the taxonomy, badly, lifts little."""
    anchor = _member(mu=0.70, observed=("ships_to_users",))
    good = team.assemble(COMPANY, T0, (anchor, _member(mu=0.90, observed=("technical_depth",))))
    bad = team.assemble(COMPANY, T0, (anchor, _member(mu=0.10, observed=("technical_depth",))))
    assert good.complementarity > bad.complementarity


def test_complementarity_is_capped() -> None:
    """No amount of composition may outrun the measurement precision it adjusts."""
    anchor = _member(mu=0.70, observed=("ships_to_users",))
    crowd = tuple(
        _member(mu=1.0, observed=(trait,)) for trait in ALL_TRAITS if trait != "ships_to_users"
    )
    ts = team.assemble(COMPANY, T0, (anchor, *crowd))
    assert ts.complementarity_lift == pytest.approx(team.COMPLEMENTARITY_CAP)
    assert ts.mu <= 1.0


def test_mu_is_reconstructible_from_the_published_components() -> None:
    """The aggregation must be visible in the output, not buried."""
    ts = team.assemble(
        COMPANY,
        T0,
        (
            _member(mu=0.62, observed=("ships_to_users",)),
            _member(mu=0.51, observed=("technical_depth",)),
        ),
    )
    assert ts.mu == pytest.approx(min(ts.anchor_mu + ts.complementarity_lift, 1.0))


def test_only_observed_traits_earn_complementarity() -> None:
    """The independent-channel gate carries over: evidenced-but-unobserved earns nothing.

    This is the Type 5 defence. A co-founder whose only 'complementary' trait rests on
    keyword-stuffed self-attested copy has not passed the gate and must not manufacture
    a lift. See docs/TRAITS.md §3.
    """
    anchor = _member(mu=0.70, observed=("ships_to_users",))
    unobserved = team.Member(
        entity_id=uuid4(),
        mu=0.60,
        band=0.1,
        trend=0.0,
        observed_traits=(),  # nothing cleared its min_channels gate
        evidenced_traits=("technical_depth", "problem_scoping"),
        contributing_event_ids=(uuid4(),),
    )
    ts = team.assemble(COMPANY, T0, (anchor, unobserved))
    assert ts.complementarity == 0.0


# --- uncertainty ---------------------------------------------------------------------


def test_team_band_is_never_tighter_than_its_widest_member() -> None:
    """The core uncertainty invariant. An aggregate of uncertain things is not certain."""
    members = (
        _member(mu=0.8, band=0.06, observed=("ships_to_users",)),
        _member(mu=0.5, band=0.19, observed=("technical_depth",)),
        _member(mu=0.4, band=0.11, observed=("problem_scoping",)),
    )
    ts = team.assemble(COMPANY, T0, members)
    assert ts.band >= max(m.band for m in members)


def test_disagreement_between_members_widens_the_band() -> None:
    agree = team.assemble(
        COMPANY,
        T0,
        (_member(mu=0.60, band=0.1), _member(mu=0.60, band=0.1, observed=("technical_depth",))),
    )
    disagree = team.assemble(
        COMPANY,
        T0,
        (_member(mu=0.90, band=0.1), _member(mu=0.20, band=0.1, observed=("technical_depth",))),
    )
    assert disagree.band > agree.band


def test_band_never_exceeds_the_prior() -> None:
    ts = team.assemble(
        COMPANY, T0, (_member(mu=1.0, band=0.49), _member(mu=0.0, band=0.49))
    )
    assert ts.band <= team._PRIOR_SD


def test_team_axis_confidence_never_beats_the_most_certain_member() -> None:
    from intelligence import screen
    from schema.events import FounderScore

    members = (
        _member(mu=0.8, band=0.05, observed=("ships_to_users",)),
        _member(mu=0.4, band=0.20, observed=("technical_depth",)),
    )
    ts = team.assemble(COMPANY, T0, members)
    tightest = min(members, key=lambda m: m.band)
    solo_axis = screen.founder_axis(
        FounderScore(entity_id=tightest.entity_id, as_of=T0, mu=tightest.mu, band=tightest.band, trend=0.0)
    )
    assert team.team_axis(ts).confidence <= solo_axis.confidence


# --- role split ----------------------------------------------------------------------


def test_role_split_is_not_determinable_and_says_why() -> None:
    """No school, no employer, no title (SHARED #3) — so no technical/business split."""
    ts = team.assemble(
        COMPANY,
        T0,
        (_member(mu=0.7, observed=("technical_depth",)), _member(mu=0.6, observed=("ships_to_users",))),
    )
    assert ts.role_split == team.NOT_DETERMINABLE
    assert ts.role_split_reason.strip()
    # Solo founders get the same honest answer, not a different one.
    assert team.assemble(COMPANY, T0, (_member(mu=0.7),)).role_split == team.NOT_DETERMINABLE


def test_no_pedigree_vocabulary_leaks_into_the_output() -> None:
    """Invariant #3 as a string check over everything the API will render."""
    ts = team.assemble(
        COMPANY, T0, (_member(mu=0.7, observed=("technical_depth",)), _member(mu=0.6))
    )
    blob = repr(team.as_dict(ts)).lower()
    for banned in ("stanford", "mit", "google", "sequoia", "y combinator", "alma mater", "degree"):
        assert banned not in blob


def test_as_dict_publishes_the_aggregation_choice() -> None:
    ts = team.assemble(
        COMPANY, T0, (_member(mu=0.7, observed=("technical_depth",)), _member(mu=0.6))
    )
    out = team.as_dict(ts)
    assert out["aggregation"]["method"] == "anchor_plus_complementarity"
    assert out["aggregation"]["mean_mu"] == pytest.approx(ts.mean_mu)
    assert out["uncertainty"]["band"] >= out["uncertainty"]["widest_member_band"]
    assert out["n_founders"] == 2
