"""Scanner parsing, against saved fixtures. Owner: B.

No network here on purpose — a test that needs the internet is a test that fails at
hour 19. Fixtures under tests/fixtures/ are trimmed copies of real responses.

What each test is really guarding: observed_at comes from the source's own clock.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.search import SearchResult
from schema.events import EventKind, Source
from sourcing import bus
from sourcing.scanners import arxiv, github, hn, web

FIXTURES = Path(__file__).parent / "fixtures"


def _json(name: str):
    return json.loads((FIXTURES / name).read_text())


def _at(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# HN
# ---------------------------------------------------------------------------


def test_hn_parses_posts_comments_and_skips_empty_hits() -> None:
    signals = hn.parse(_json("hn_search.json"))

    assert len(signals) == 3  # the empty-content hit is dropped, not ingested blank
    assert all(s.source is Source.HN for s in signals)

    post = signals[0]
    assert post.meta["kind"] == str(EventKind.HN_POST)
    assert post.meta["author"] == "Palmik"
    assert post.meta["points"] == 550
    assert bus.parse_ts(post.meta["observed_at"]) == _at(2025, 4, 14, 15, 3, 10)

    comment = signals[2]
    assert comment.meta["kind"] == str(EventKind.HN_COMMENT)
    assert "scheduler" in comment.content


def test_hn_event_keeps_the_source_timestamp() -> None:
    event = bus.ingest(hn.parse(_json("hn_search.json"))[0])[-1]
    assert event.kind is EventKind.HN_POST
    assert event.observed_at == _at(2025, 4, 14, 15, 3, 10)
    assert bus.DATE_INFERRED not in event.integrity_flags
    assert event.source_url


def test_hn_falls_back_to_an_api_url_when_the_story_has_no_link() -> None:
    assert hn.parse(_json("hn_search.json"))[2].source_url.startswith(hn.ITEM_API)


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------


def test_arxiv_uses_v1_submission_date_and_keeps_affiliation_as_a_fact() -> None:
    signals = arxiv.parse((FIXTURES / "arxiv_atom.xml").read_text())

    assert len(signals) == 2
    paper = signals[0]
    assert paper.source is Source.ARXIV
    assert paper.meta["kind"] == str(EventKind.PAPER)
    assert paper.meta["arxiv_id"].endswith("v1")
    assert paper.meta["categories"]

    published = bus.parse_ts(paper.meta["observed_at"])
    assert published is not None and published.year >= 2020

    # Affiliation is stored, and its key says out loud that it is not a scoring input.
    author = paper.meta["authors"][0]
    assert set(author) == {"name", "affiliation_fact_only"}
    assert author["affiliation_fact_only"] == "Some Research Lab"


def test_arxiv_event_stamps_the_submission_date() -> None:
    signal = arxiv.parse((FIXTURES / "arxiv_atom.xml").read_text())[0]
    event = bus.ingest(signal)[-1]
    assert event.kind is EventKind.PAPER
    assert event.observed_at == bus.parse_ts(signal.meta["observed_at"])
    assert bus.DATE_INFERRED not in event.integrity_flags


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


@pytest.fixture
def gh(monkeypatch):
    """Routes github's REST calls to fixtures. Unknown paths return None, as a failure would."""
    routes = {
        "/users/anaruiz": "github_user.json",
        "/users/anaruiz/repos": "github_repos.json",
        "/repos/anaruiz/tinyjit/commits": "github_commits.json",
        "/repos/anaruiz/tinyjit/releases": "github_releases.json",
        "/repos/anaruiz/tinyjit/languages": "github_languages.json",
        "/repos/anaruiz/tinyjit/contributors": "github_contributors.json",
    }
    calls: list[str] = []

    def fake_fetch_json(url, params=None, **kw):
        path = url.replace(github.API, "")
        calls.append(path)
        if path not in routes:
            raise bus.FetchError(404, f"no fixture for {path}")
        return _json(routes[path])

    monkeypatch.setattr(bus, "fetch_json", fake_fetch_json)
    return calls


def test_github_scan_builds_profile_repo_commit_and_release_signals(gh) -> None:
    signals = github.scan("anaruiz", limit=50)
    kinds = [s.meta["kind"] for s in signals]

    assert kinds.count(str(EventKind.PROFILE_FACT)) == 1
    assert kinds.count(str(EventKind.RELEASE)) == 1
    assert kinds.count(str(EventKind.REPO_ACTIVITY)) >= 3  # 2 repos + 2 commits
    assert all(s.source is Source.GITHUB for s in signals)


def test_github_observed_at_is_the_author_date_not_the_committer_date(gh) -> None:
    signals = github.scan("anaruiz", limit=50)
    commit = next(s for s in signals if s.meta.get("sha"))
    event = bus.ingest(commit)[-1]

    assert event.observed_at == _at(2024, 5, 20, 17, 58, 11)  # author date
    assert event.observed_at != _at(2024, 6, 1, 12, 0)  # committer date is later; never used
    assert event.evidence_span.startswith("anaruiz/tinyjit@9f2c1ab")
    assert bus.DATE_INFERRED not in event.integrity_flags


def test_github_keeps_fork_lineage_languages_and_contributors(gh) -> None:
    signals = github.scan("anaruiz", limit=50)
    repos = [s for s in signals if s.meta.get("repo") and "sha" not in s.meta]
    main = next(s for s in repos if s.meta["repo"] == "anaruiz/tinyjit")
    assert main.meta["languages"] == {"Rust": 184320, "C": 40211}
    assert main.meta["contributors"] == ["anaruiz", "kmori"]

    forked = next(s for s in repos if s.meta["repo"] == "anaruiz/llvm-notes")
    assert forked.meta["fork"] is True
    assert forked.meta["fork_parent"] == "llvm/llvm-project"


def test_github_profile_uses_account_creation_date(gh) -> None:
    profile = github.scan("anaruiz", limit=50)[0]
    assert bus.ingest(profile)[-1].observed_at == _at(2016, 2, 11, 8, 22, 14)


def test_github_degrades_to_partial_results_when_rate_limited(monkeypatch) -> None:
    """Unauthenticated is 60 req/hr. Hitting the wall must not raise into the pipeline."""
    calls = {"n": 0}

    def flaky(url, params=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _json("github_user.json")
        raise bus.RateLimited(403, "rate limited by api.github.com")

    monkeypatch.setattr(bus, "fetch_json", flaky)
    signals = github.scan("anaruiz", limit=50)
    assert len(signals) == 1  # the profile survived; the repo fan-out stopped cleanly


def test_github_returns_nothing_for_an_unknown_login(gh) -> None:
    assert github.scan("nobody-here", limit=10) == []


# ---------------------------------------------------------------------------
# Web (Tavily enrichment)
# ---------------------------------------------------------------------------


@pytest.fixture
def tavily(monkeypatch):
    results = [SearchResult(**r) for r in _json("web_results.json")]
    monkeypatch.setattr(web, "search", lambda q, max_results=5: results[:max_results])
    return results


def test_web_dedupes_across_query_templates(tavily) -> None:
    signals = web.scan("Ana Ruiz", limit=20)
    urls = [s.source_url for s in signals]
    assert len(urls) == len(set(urls))
    assert all(s.source is Source.WEB for s in signals)


def test_web_extracts_a_date_floor_from_the_url_path(tavily) -> None:
    signal = _by_url(web.scan("Ana Ruiz", limit=20), "anaruiz.dev")
    event = bus.ingest(signal)[-1]

    assert signal.meta["date_floor"] == "2021-03-09"
    assert event.observed_at == _at(2021, 3, 9)
    assert bus.DATE_INFERRED in event.integrity_flags  # inferred, and it says so


def test_web_extracts_a_date_floor_from_the_snippet(tavily) -> None:
    signal = _by_url(web.scan("Ana Ruiz", limit=20), "devconf.example.org")
    assert signal.meta["date_floor"] == "2022-02-03"
    assert bus.DATE_INFERRED in bus.ingest(signal)[-1].integrity_flags


def test_web_with_no_date_anywhere_is_flagged_not_stamped_now(tavily) -> None:
    signal = _by_url(web.scan("Ana Ruiz", limit=20), "linkedin.com")
    event = bus.ingest(signal)[-1]

    assert signal.meta["date_floor"] is None
    assert event.observed_at == signal.fetched_at
    assert bus.DATE_INFERRED in event.integrity_flags
    assert event.payload["self_published"] is True  # weighs below independent sources


def test_web_trusts_a_real_publish_date_when_tavily_supplies_one(tavily) -> None:
    signal = _by_url(web.scan("Ana Ruiz", limit=20), "blog.example.com")
    event = bus.ingest(signal)[-1]

    assert event.observed_at == _at(2023, 6, 15)
    assert bus.DATE_INFERRED not in event.integrity_flags


def _by_url(signals, host: str):
    return next(s for s in signals if host in s.source_url)
