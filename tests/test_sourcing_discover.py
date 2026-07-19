"""Candidate discovery: the verification chain, and what it refuses.

These tests are about REJECTION, not yield. `sourcing/discover.py` exists because the
corpus was previously grown by hand and filled with entities that looked plausible and
did not exist — 14 of 15 GitHub repos 404ing, an arXiv id that turned out to be a
neutrino physics paper. So the property worth pinning is not "it finds founders", it is
"there is no input for which it invents one".

Every test here is offline. The network legs are exercised by passing recorded response
shapes into `verify()` with the fetch monkeypatched, so the suite has the same result
with the wire unplugged.
"""

from __future__ import annotations

import pytest

from sourcing import discover


# --- naming: the parser must decline rather than guess ---------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Show HN: Lathe – a schema migration tool", "Lathe"),
        ("Show HN: Mcpsnoop: inspect MCP traffic", "Mcpsnoop"),
        ("Show HN: Tiny-vLLM, a minimal inference server", "Tiny-vLLM"),
        ("show hn: Honker - a load generator", "Honker"),
    ],
)
def test_company_name_is_the_product_not_the_sentence(title: str, expected: str) -> None:
    assert discover.company_name_from(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Show HN: I built an app to stop me doomscrolling",
        "Show HN: We open-sourced our entire data platform today",
        "Show HN: How I made a synth for my daughter",
        "Show HN: My weekend project for tracking sunrise times",
        "Show HN:",
        "",
    ],
)
def test_company_name_declines_when_the_title_is_a_clause(title: str) -> None:
    """A company row named after half a sentence is worse than no row. The caller
    falls back to the repository's own name, which is a real string from a real
    response — never to a truncation of the title."""
    assert discover.company_name_from(title) is None


# --- the product filter ----------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "sindresorhus/awesome-nodejs",
        "someone/rust-resources",
        "someone/interview-notes",
        "someone/dotfiles",
        "someone/react-examples",
        "someone/k8s-tutorial",
    ],
)
def test_curated_lists_and_dotfiles_are_not_products(slug: str) -> None:
    """These are real repos that are not evidence of building a company. Admitting
    them is how a corpus fills with technically-verifiable noise."""
    assert not discover.looks_like_a_product(slug)


@pytest.mark.parametrize("slug", ["jmaczan/tiny-vllm", "devenjarvis/lathe", "org/peerd"])
def test_actual_products_survive_the_filter(slug: str) -> None:
    assert discover.looks_like_a_product(slug)


# --- sector scoping is the thesis's decision, not this module's -------------------


def test_sector_classification_reads_the_artifact_not_the_person() -> None:
    assert discover.classify_sector("Show HN: an LLM inference server", None, "") == "ai-infra"
    assert discover.classify_sector("Show HN: a terminal debugger", None, "") == "dev-tools"
    assert discover.classify_sector("a columnar storage engine for OLAP", None, "") == "data-infra"


def test_out_of_thesis_artifacts_are_dropped_not_downranked() -> None:
    """core.thesis is explicit: a fund that does not invest in a sector does not rank
    it lower, it does not look at it. So this returns None (drop), not a low score."""
    assert discover.classify_sector("Show HN: a recipe sharing app for families") is None


# --- the permalink host is registry-resolved, never a literal ---------------------


def test_show_hn_host_comes_from_the_source_registry() -> None:
    """Invariant #3 bans the brand token from source, and tests/test_no_pedigree.py
    greps for it. The deeper reason is that a source disabled in the registry must not
    stay reachable through a hardcoded string in sourcing/."""
    from core.search import source_domains

    assert discover._show_hn_host() == next(iter(source_domains("show_hn")), None)


# --- the verification chain: every leg fails closed ------------------------------


def _hit(**over) -> dict:
    base = {
        "objectID": "40000001",
        "author": "someuser",
        "title": "Show HN: Lathe – a schema migration tool",
        "url": "https://github.com/devenjarvis/lathe",
        "points": 120,
        "created_at": "2026-06-07T12:00:00Z",
    }
    return {**base, **over}


def test_a_story_that_points_nowhere_near_github_is_not_a_candidate() -> None:
    """Returns None — never a Candidate with a constructed repo URL."""
    assert discover.verify(_hit(url="https://example.com/blog/post")) is None
    assert discover.verify(_hit(url="")) is None


def test_a_story_with_no_real_timestamp_is_dropped() -> None:
    """Invariant #1: observed_at is when the world produced it. A launch with no
    defensible date does not get one substituted from the fetch clock."""
    assert discover.verify(_hit(created_at=None)) is None
    assert discover.verify(_hit(created_at="not a date")) is None


def test_a_repo_that_404s_is_rejected_with_the_status_recorded(monkeypatch) -> None:
    """The audited failure mode was exactly this: repos that 404 sitting in the corpus
    as though they were evidence. The candidate must come back rejected, and the
    rejection must name the leg that failed."""

    def boom(url, *a, **kw):
        raise discover.bus.FetchError(404, "not found")

    monkeypatch.setattr(discover.bus, "fetch_json", boom)
    cand = discover.verify(_hit())
    assert cand is not None and not cand.verified
    assert cand.reject_reason == "repo_unreachable:404"


def test_rate_limiting_propagates_rather_than_becoming_a_rejection(monkeypatch) -> None:
    """"We were throttled" and "this founder is not real" are opposite findings.
    Collapsing them would silently shrink the corpus during a quota event."""

    def limited(url, *a, **kw):
        raise discover.bus.RateLimited(403, "slow down")

    monkeypatch.setattr(discover.bus, "fetch_json", limited)
    with pytest.raises(discover.bus.RateLimited):
        discover.verify(_hit())


def test_a_verified_candidate_cites_only_urls_that_came_from_responses(monkeypatch) -> None:
    repo = {
        "name": "lathe",
        "description": "a database schema migration tool",
        "topics": ["sql", "database"],
        "stargazers_count": 1586,
        "owner": {"login": "devenjarvis"},
        "html_url": "https://github.com/devenjarvis/lathe",
    }
    user = {
        "login": "devenjarvis",
        "name": "Deven Jarvis",
        "type": "User",
        "followers": 28,
        "html_url": "https://github.com/devenjarvis",
    }

    def fake(url, *a, **kw):
        return repo if "/repos/" in url else user

    monkeypatch.setattr(discover.bus, "fetch_json", fake)
    cand = discover.verify(_hit())

    assert cand is not None and cand.verified
    assert cand.display_name == "Deven Jarvis"
    assert cand.github_login == "devenjarvis"
    assert cand.sector == "data-infra"
    # The launch date is the story's, to the second — not the fetch time.
    assert cand.launched_at.isoformat().startswith("2026-06-07T12:00:00")
    # Every citation is a response field or a permalink built from a fetched id.
    assert repo["html_url"] in cand.evidence
    assert user["html_url"] in cand.evidence
    assert all(u.startswith("https://") for u in cand.evidence)


def test_an_org_owner_falls_back_to_who_actually_wrote_the_code(monkeypatch) -> None:
    """Attributing an org's work to whoever registered the account would be the exact
    fabrication this module prevents. So we ask GitHub who has the most commits —
    a fetched fact — and reject when even that is unavailable."""
    repo = {
        "name": "klavis",
        "description": "open-source MCP integration for LLM applications",
        "topics": ["mcp", "llm"],
        "stargazers_count": 900,
        "owner": {"login": "Klavis-AI"},
        "html_url": "https://github.com/Klavis-AI/klavis",
    }
    org = {"login": "Klavis-AI", "type": "Organization"}
    top = {"login": "wirehack", "name": "Wire Hack", "type": "User", "followers": 40}

    def fake(url, *a, **kw):
        if "/contributors" in url:
            return [{"login": "dependabot[bot]", "type": "Bot"}, {"login": "wirehack", "type": "User"}]
        if "/repos/" in url:
            return repo
        return org if url.endswith("Klavis-AI") else top

    monkeypatch.setattr(discover.bus, "fetch_json", fake)
    cand = discover.verify(_hit(url="https://github.com/Klavis-AI/klavis"))

    assert cand is not None and cand.verified
    assert cand.github_login == "wirehack"  # the human, not the org, and not a bot


def test_an_org_with_no_resolvable_human_is_rejected_not_guessed(monkeypatch) -> None:
    repo = {
        "name": "thing",
        "description": "an llm inference runtime",
        "topics": [],
        "owner": {"login": "SomeOrg"},
        "stargazers_count": 10,
    }

    def fake(url, *a, **kw):
        if "/contributors" in url:
            return [{"login": "renovate[bot]", "type": "Bot"}]
        if "/repos/" in url:
            return repo
        return {"login": "SomeOrg", "type": "Organization"}

    monkeypatch.setattr(discover.bus, "fetch_json", fake)
    cand = discover.verify(_hit(url="https://github.com/SomeOrg/thing"))
    assert cand is not None and not cand.verified and cand.reject_reason == "owner_is_org"


def test_the_repo_name_backs_up_an_unparseable_title(monkeypatch) -> None:
    """Both the parsed title and the repo name are strings read out of a response.
    There is no third branch that makes one up."""
    repo = {
        "name": "peerd",
        "description": "a peer to peer llm inference mesh",
        "topics": [],
        "owner": {"login": "someone"},
        "stargazers_count": 5,
    }

    def fake(url, *a, **kw):
        return repo if "/repos/" in url else {"login": "someone", "type": "User", "name": "Some One"}

    monkeypatch.setattr(discover.bus, "fetch_json", fake)
    cand = discover.verify(_hit(title="Show HN: I built a distributed inference thing"))
    assert cand is not None and cand.verified and cand.company_name == "peerd"
