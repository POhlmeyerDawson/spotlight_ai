"""github scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.

REST, and it must work with GITHUB_TOKEN empty: unauthenticated is 60 requests/hour,
so the repo fan-out is capped and a rate limit ends the scan with partial results and a
clear error rather than a hang or a stack trace.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.config import settings
from schema.events import EventKind, RawSignal, Source
from sourcing import bus

log = logging.getLogger(__name__)

API = "https://api.github.com"
CACHE = Path("data/raw/github")

MAX_REPOS = 3  # 60 req/hr unauthenticated: 2 + 4*MAX_REPOS keeps a full scan affordable
MAX_COMMITS = 30


def scan(query: str, limit: int = 50) -> list[RawSignal]:
    """query is a GitHub login. Partial results beat no results — see _get."""
    login = query.strip().lstrip("@")
    signals: list[RawSignal] = []

    user = _get(f"/users/{login}")
    if user is None:
        return []
    signals.append(_profile(user, login))

    repos = _get(f"/users/{login}/repos", {"sort": "pushed", "per_page": MAX_REPOS * 2}) or []
    for repo in repos[:MAX_REPOS]:
        signals.extend(_repo_signals(repo, login))
        if len(signals) >= limit:
            break
    return signals[:limit]


def _repo_signals(repo: dict, login: str) -> list[RawSignal]:
    full = repo.get("full_name", "")
    out = [_repo_activity(repo, login)]

    for c in _get(f"/repos/{full}/commits", {"author": login, "per_page": MAX_COMMITS}) or []:
        out.append(_commit(c, full))
    for r in _get(f"/repos/{full}/releases", {"per_page": 5}) or []:
        out.append(_release(r, full))
    return out


def _profile(user: dict, login: str) -> RawSignal:
    return RawSignal(
        source=Source.GITHUB,
        source_url=user.get("html_url"),
        content="\n".join(p for p in (user.get("name"), user.get("bio")) if p) or login,
        meta={
            "kind": str(EventKind.PROFILE_FACT),
            "observed_at": user.get("created_at"),  # account creation, a real timestamp
            "login": login,
            "followers": user.get("followers"),
            "public_repos": user.get("public_repos"),
            "location": user.get("location"),
            "blog": user.get("blog"),
            "evidence_span": f"github user {login}",
        },
    )


def _repo_activity(repo: dict, login: str) -> RawSignal:
    full = repo.get("full_name", "")
    languages = _get(f"/repos/{full}/languages") or {}
    contributors = _get(f"/repos/{full}/contributors", {"per_page": 10}) or []
    parent = (repo.get("parent") or {}).get("full_name")
    return RawSignal(
        source=Source.GITHUB,
        source_url=repo.get("html_url"),
        content=f"{full}\n{repo.get('description') or ''}",
        meta={
            "kind": str(EventKind.REPO_ACTIVITY),
            "observed_at": repo.get("created_at"),
            "login": login,
            "repo": full,
            "stars": repo.get("stargazers_count"),
            "pushed_at": repo.get("pushed_at"),
            "languages": languages,
            "contributors": [c.get("login") for c in contributors if isinstance(c, dict)],
            "fork": repo.get("fork"),
            "fork_parent": parent,  # lineage feeds the graph's fork edges
            "evidence_span": full,
        },
    )


def _commit(c: dict, full: str) -> RawSignal:
    commit = c.get("commit") or {}
    sha = c.get("sha", "")
    return RawSignal(
        source=Source.GITHUB,
        source_url=c.get("html_url"),
        content=commit.get("message", ""),
        meta={
            "kind": str(EventKind.REPO_ACTIVITY),
            # author date, not committer date: it is when the work was actually done
            "observed_at": (commit.get("author") or {}).get("date"),
            "repo": full,
            "sha": sha,
            "author_login": (c.get("author") or {}).get("login"),
            "evidence_span": f"{full}@{sha[:12]}",
        },
    )


def _release(r: dict, full: str) -> RawSignal:
    return RawSignal(
        source=Source.GITHUB,
        source_url=r.get("html_url"),
        content=f"{r.get('name') or r.get('tag_name') or ''}\n{r.get('body') or ''}",
        meta={
            "kind": str(EventKind.RELEASE),
            "observed_at": r.get("published_at") or r.get("created_at"),
            "repo": full,
            "tag": r.get("tag_name"),
            "evidence_span": f"{full} release {r.get('tag_name')}",
        },
    )


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:  # empty in this environment; unauthenticated must still work
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


def _get(path: str, params: dict | None = None):
    """Returns None on any failure. Callers degrade to partial results, never to a crash."""
    try:
        return bus.fetch_json(f"{API}{path}", params, cache_dir=CACHE, headers=_headers())
    except bus.RateLimited as exc:
        log.warning("github rate limit hit at %s — returning partial results: %s", path, exc)
        return None
    except bus.FetchError as exc:
        log.warning("github fetch failed at %s: %s", path, exc)
        return None
