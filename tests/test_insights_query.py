"""The compound query's parse contract.

The behaviour under test is mostly about HONESTY rather than recall. The filter this
route runs is allowed to be narrow; it is not allowed to claim it applied a constraint
it did not, and it is never allowed to answer an unrecognised query with the whole
list. That last one was the shipped behaviour: an eight-word sector vocabulary meant
"climate hardware" parsed to `sectors: None`, matched all 13 companies, and printed
"no filters recognised" underneath — a full result set presented as an answer.
"""

from __future__ import annotations

import pytest

from api.routers.insights import (
    _coerce_filter,
    _describe_filter,
    _filter_warnings,
    _keyword_filter,
    _matches,
)

ROWS = [
    {
        "id": "ai-1",
        "name": "Tensorpage",
        "sector": "ai-infra",
        "archetype_label": "Visible Builder",
        "one_liner": "Paged KV-cache allocator for long-context inference.",
        "axes": {"founder": {"score": 0.81, "trend": 0.06}},
        "gate": "proceed",
        "flags": [],
        "unverified_claims": 0,
    },
    {
        "id": "data-1",
        "name": "Tallwind",
        "sector": "data-infra",
        "archetype_label": "Contradiction",
        "one_liner": "Usage metering and billing primitives.",
        "axes": {"founder": {"score": 0.62, "trend": 0.04}},
        "gate": "proceed",
        "flags": ["x"],
        "unverified_claims": 1,
    },
    {
        "id": "cold-1",
        "name": "Veritanode",
        "sector": "ai-infra",
        "archetype_label": "Cold Start",
        "one_liner": "Cross-region vector index on commodity NVMe.",
        "axes": {"founder": {"score": 0.55, "trend": -0.02}},
        "gate": "proof_protocol",
        "flags": ["a", "b"],
        "unverified_claims": 2,
    },
]


def parse(q: str) -> dict:
    return _coerce_filter(_keyword_filter(q))


def run(q: str) -> list[str]:
    f = parse(q)
    out = [r["id"] for r in ROWS if _matches(r, f)]
    limit = f.get("limit")
    return out[:limit] if isinstance(limit, int) and limit > 0 else out


# --- the vocabulary is open -------------------------------------------------


@pytest.mark.parametrize(
    "q,sector",
    [
        ("climate hardware", "climate hardware"),
        ("consumer social startups", "consumer social"),
        ("defense logistics", "defense logistics"),
        ("vertical saas for dentists", "vertical saas dentists"),
    ],
)
def test_an_industry_the_system_has_never_seen_still_parses(q: str, sector: str) -> None:
    """No hardcoded sector list. Whatever the investor names becomes the filter."""
    assert parse(q)["sectors"] == [sector]


def test_an_unknown_industry_returns_nothing_and_says_why() -> None:
    """The regression that mattered most: this used to return every company.

    Zero rows is the correct answer for a portfolio with no climate hardware in it.
    Returning all 13 while printing "no filters recognised" was a wrong answer
    presented as a complete one.
    """
    assert run("climate hardware") == []

    warnings = _filter_warnings(parse("climate hardware"), ROWS)
    assert any("climate hardware" in w and "coverage" in w for w in warnings), warnings


def test_a_sector_that_does_exist_is_not_warned_about() -> None:
    assert not any("coverage" in w for w in _filter_warnings(parse("ai infra"), ROWS))


def test_readback_never_silently_claims_an_unfiltered_list_is_an_answer() -> None:
    described = _describe_filter(_coerce_filter({}))
    assert "unfiltered" in described and "every record" in described


# --- the new structural clauses ---------------------------------------------


def test_stage_geo_and_cheque_are_all_parsed_from_one_sentence() -> None:
    f = parse("seed-stage fintech in Europe, cheque under $2M")
    assert f["sectors"] == ["fintech"]
    assert f["stages"] == ["seed"]
    assert f["geos"] == ["europe"]
    assert f["check_size_max_usd"] == 2_000_000
    assert f["check_size_min_usd"] is None


@pytest.mark.parametrize(
    "q,expected",
    [
        ("pre-seed companies", ["pre-seed"]),
        ("series a rounds", ["series-a"]),
        ("growth stage", ["growth"]),
    ],
)
def test_stage_vocabulary(q: str, expected: list[str]) -> None:
    assert parse(q)["stages"] == expected


@pytest.mark.parametrize(
    "q,lo,hi",
    [
        ("cheque under $2M", None, 2_000_000),
        ("tickets above $500k", 500_000, None),
        ("raising between $500k and $1.5m", 500_000, 1_500_000),
        ("no money mentioned", None, None),
    ],
)
def test_cheque_size(q: str, lo: float | None, hi: float | None) -> None:
    f = parse(q)
    assert (f["check_size_min_usd"], f["check_size_max_usd"]) == (lo, hi)


def test_a_clause_the_records_carry_no_data_for_does_not_exclude_them() -> None:
    """Absent metadata is not disqualifying — the same rule core/thesis.in_scope
    applies. These rows have no stage field, so the stage clause cannot narrow them."""
    assert parse("seed stage")["stages"] == ["seed"]
    assert run("seed stage") == ["ai-1", "data-1", "cold-1"]


def test_but_a_clause_that_could_not_be_applied_is_reported() -> None:
    warnings = _filter_warnings(parse("seed stage"), ROWS)
    assert any("stage was not applied to 3 of 3" in w for w in warnings), warnings


def test_cheque_size_points_the_user_at_the_standing_thesis() -> None:
    """Cheque size is a property of the fund, not of a company. When no round size is
    recorded the readback has to say where that constraint actually belongs."""
    warnings = _filter_warnings(parse("cheque under $2M"), ROWS)
    assert any("standing thesis" in w for w in warnings), warnings


# --- clauses that cannot be executed are named, not dropped -----------------


def test_a_claim_subject_is_reported_as_not_understood() -> None:
    """"unverified revenue" filters on verification but NOT on the claim being about
    revenue, because the ranked row stores a count rather than claim text."""
    f = parse("companies with unverified revenue")
    assert f["verification"] == "unverified"
    assert f["unparsed"] and "revenue" in f["unparsed"][0]
    assert "NOT UNDERSTOOD" in _describe_filter(f)
    assert run("companies with unverified revenue") == ["data-1", "cold-1"]


def test_an_unknown_key_from_the_model_is_surfaced_not_swallowed() -> None:
    f = _coerce_filter({"sectors": ["ai"], "founder_went_to_stanford": True})
    assert f["unparsed"] and "founder_went_to_stanford" in f["unparsed"][0]


def test_revenue_is_only_a_claim_subject_when_verification_was_asked_for() -> None:
    assert parse("revenue infrastructure companies")["sectors"] == ["revenue infrastructure"]


# --- the existing clauses still work ----------------------------------------


def test_gate_trend_flags_and_limit() -> None:
    assert run("cold start companies routed to proof protocol") == ["cold-1"]
    assert run("rising trend") == ["ai-1", "data-1"]
    assert run("companies with integrity flags") == ["data-1", "cold-1"]
    assert run("top 2 companies") == ["ai-1", "data-1"]


def test_founder_score_reads_the_founder_axis() -> None:
    """`score` lives at axes.founder.score on a ranked row. Reading only the flat keys
    made every "founder score above X" query return nothing at all."""
    assert parse("founder score above 0.7")["min_score"] == 0.7
    assert run("founder score above 0.7") == ["ai-1"]


def test_a_percentage_score_is_read_on_the_same_0_to_1_scale() -> None:
    assert parse("founder score above 70")["min_score"] == 0.7


def test_the_deterministic_path_admits_it_is_degraded() -> None:
    """The demo depends on degrading gracefully, but the readback has to say so —
    otherwise a fallback parse is indistinguishable from a model parse on stage."""
    from api.routers.insights import _parse_filter

    f = _parse_filter("ai infra")
    if f.get("degraded"):
        assert any("without a language model" in w for w in _filter_warnings(f, ROWS))
