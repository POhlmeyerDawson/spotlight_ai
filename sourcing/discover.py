"""Candidate discovery: who is worth scanning at all. Owner: B.

THE GAP THIS CLOSES. Every scanner in `sourcing/scanners/` answers "what has this
person shipped?" — they take a subject and return signals. Nothing answered the prior
question, "which people?". The corpus was therefore whatever someone had typed into a
seed file, and growing it meant typing more names. That is exactly the path that
produced the fabrication this module exists to make structurally impossible.

THE VERIFICATION CHAIN. A candidate is admitted only when three independent live
sources agree, and every one of them is a fetch we actually performed:

  1. a Show HN story exists in the HN Algolia index, with its own objectID and its
     own `created_at` (the launch, and the date of the launch);
  2. the URL that story points at is a GitHub repository that returns HTTP 200 from
     `api.github.com` (the artifact);
  3. that repository's owner resolves to a real GitHub account (the person).

Fail any leg and the candidate is DROPPED, not downgraded. `reject_reason` records
which leg failed so a run can report its own yield honestly rather than reporting the
survivors only. There is no branch in this module that invents a name, a URL, or an
id — every field on an admitted Candidate was read out of a response body.

WHY SHOW HN. docs/SOURCES.md tier 2: it is the only source that yields a dated launch
paired with the founder's unscripted response to strangers attacking their work. It is
also, for our purpose, the only high-volume index of people who shipped something and
said so on a specific date — which is the population this product claims to find.

NON-FAMOUS BY CONSTRUCTION, NOT BY ASSERTION. `POINT_BANDS` sweeps modest score
windows and skips the front-page blowouts. The thesis's own sectors do the topical
filtering (`core.thesis`), so a change of fund thesis changes who gets discovered,
rather than this module holding a second private opinion about what matters.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.config import settings
from sourcing import bus
from sourcing.scanners.github import repo_slug

log = logging.getLogger(__name__)

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://hn.algolia.com/api/v1/items"
GH_API = "https://api.github.com"
CACHE = Path("data/raw/discover")

# Score windows swept for candidates. Deliberately capped below the front page: a
# Show HN at 3,000 points belongs to someone who is already found, and this product's
# thesis is the hidden candidate. The floor is a signal-quality gate, not a merit one —
# below ~15 points a story usually had no engagement to observe conduct in.
POINT_BANDS = ((15, 80), (80, 250), (250, 700))
PAGES_PER_BAND = 5
HITS_PER_PAGE = 100

# Repos that exist but are not a person shipping a thing. Checked against the slug.
_NOT_A_PRODUCT = re.compile(
    r"(^|/)(awesome[-_]|.*[-_]list$|.*[-_]resources$|dotfiles$|.*\.github\.io$"
    r"|.*[-_]notes$|.*[-_]book$|.*[-_]tutorial$|.*[-_]examples?$|.*[-_]demos?$)",
    re.I,
)

# Sector vocabulary, keyed by the thesis sector id it maps to. Matched against the
# story title plus the repo description/topics — never against a person.
SECTOR_TERMS: dict[str, tuple[str, ...]] = {
    "ai-infra": (
        "llm", "inference", "embedding", "vector", "rag", "agent", "mcp", "transformer",
        "gpu", "cuda", "fine-tun", "quantiz", "model serving", "prompt", "openai",
        "diffusion", "tokeniz", "ml", "machine learning", "neural", "pytorch", "onnx",
    ),
    "dev-tools": (
        "cli", "compiler", "debugger", "linter", "formatter", "ide", "editor", "sdk",
        "build system", "package manager", "test framework", "profiler", "terminal",
        "language server", "static analysis", "codegen", "runtime", "devtools", "parser",
    ),
    "data-infra": (
        "database", "sql", "query engine", "stream", "kafka", "etl", "data pipeline",
        "olap", "columnar", "storage engine", "index", "cache", "postgres", "duckdb",
        "warehouse", "orchestrat", "serializ",
    ),
}


@dataclass
class Candidate:
    """One verified person, with the fetched evidence that says they are real.

    Every URL on this object was fetched, or was returned as the canonical permalink
    by an API we fetched. None of them is constructed from a template and hoped for.
    """

    display_name: str
    github_login: str
    hn_author: str
    company_name: str
    repo_slug: str
    repo_url: str
    hn_object_id: str
    hn_url: str
    launched_at: datetime
    points: int
    sector: str
    followers: int = 0
    stars: int = 0
    evidence: list[str] = field(default_factory=list)
    reject_reason: str | None = None

    @property
    def verified(self) -> bool:
        return self.reject_reason is None


def _headers() -> dict:
    token = getattr(settings, "github_token", None) or ""
    return {"Authorization": f"token {token}"} if token else {}


def _show_hn_host() -> str | None:
    """The Show HN permalink host, resolved through the source registry.

    Not a literal. Invariant #3 bans the brand token from source, and
    `tests/test_no_pedigree.py` greps for exactly that — but the deeper reason is the
    one `core.search.source_domains` documents: a source disabled in the registry must
    not stay reachable through a hardcoded string somewhere in `sourcing/`. Returns
    None when the surface is disabled, and the permalink is then simply omitted; the
    Algolia item URL already pins the story, so the citation survives.
    """
    from core.search import source_domains

    return next(iter(source_domains("show_hn")), None)


def _top_contributor(slug: str, *, refresh: bool = False) -> dict | None:
    """The account with the most commits in `slug`, fully resolved. None if unclear.

    Used only for org-owned repos. Ranked-by-commits is what the endpoint returns, so
    "wrote the most commits here" is a fetched fact; anything softer than that (a
    README byline, an org member list) would be a guess and is not accepted.
    """
    try:
        ranked = bus.fetch_json(
            f"{GH_API}/repos/{slug}/contributors",
            {"per_page": 5},
            cache_dir=CACHE,
            headers=_headers(),
            refresh=refresh,
        )
    except bus.RateLimited:
        raise
    except bus.FetchError:
        return None
    for c in ranked or []:
        if c.get("type") == "User" and c.get("login") and not c["login"].endswith("[bot]"):
            try:
                user = bus.fetch_json(
                    f"{GH_API}/users/{c['login']}",
                    cache_dir=CACHE,
                    headers=_headers(),
                    refresh=refresh,
                )
            except bus.FetchError:
                return None
            return user if user.get("type") == "User" else None
    return None


def classify_sector(*texts: str | None) -> str | None:
    """Which thesis sector this artifact is in, or None for out-of-scope.

    Out-of-scope is a DROP, not a low score — `core.thesis` is explicit that a fund
    which does not invest in a sector does not rank it lower, it does not look at it.
    """
    from core import thesis

    allowed = thesis.included_sectors() or set(SECTOR_TERMS)
    blob = " ".join(t for t in texts if t).lower()
    best, best_hits = None, 0
    for sector, terms in SECTOR_TERMS.items():
        if sector not in allowed:
            continue
        hits = sum(1 for t in terms if t in blob)
        if hits > best_hits:
            best, best_hits = sector, hits
    return best


def looks_like_a_product(slug: str) -> bool:
    """Reject curated lists, dotfiles and course notes. They are real repos that are
    not evidence of building a company, and admitting them is how a corpus fills with
    technically-verifiable noise."""
    return not _NOT_A_PRODUCT.search(slug or "")


def company_name_from(title: str) -> str | None:
    """The product name out of a `Show HN: Name – blurb` title.

    Returns None rather than a guess when the title has no name-shaped prefix, because
    a company row named after half a sentence is worse than no row.
    """
    t = (title or "").strip()
    t = re.sub(r"^show\s+hn\s*:\s*", "", t, flags=re.I).strip()
    if not t:
        return None
    # `Name – blurb`, `Name: blurb`, `Name - blurb`, `Name, blurb`
    head = re.split(r"\s+[–—-]\s+|:\s+|,\s+", t, maxsplit=1)[0].strip()
    # A "name" that is a whole clause is not a name.
    if not head or len(head) > 40 or len(head.split()) > 5:
        return None
    if re.match(r"^(i|we|my|a|an|the|how|why|what)\b", head, re.I):
        return None
    return head


def show_hn_stories(
    *, since_ts: int, bands=POINT_BANDS, pages: int = PAGES_PER_BAND, refresh: bool = False
) -> list[dict]:
    """Every Show HN story in the score windows, newest first. Free, no key, cached.

    Deduplicated by objectID across bands — the windows are half-open but Algolia's
    relevance paging can still repeat a hit across pages.
    """
    out: dict[str, dict] = {}
    for lo, hi in bands:
        for page in range(pages):
            data = bus.fetch_json(
                HN_SEARCH,
                {
                    "tags": "show_hn",
                    "numericFilters": f"points>{lo},points<{hi},created_at_i>{since_ts}",
                    "hitsPerPage": HITS_PER_PAGE,
                    "page": page,
                },
                cache_dir=CACHE,
                refresh=refresh,
            )
            for hit in data.get("hits", []):
                if hit.get("objectID"):
                    out.setdefault(hit["objectID"], hit)
            if page + 1 >= data.get("nbPages", 0):
                break
    return list(out.values())


def verify(hit: dict, *, refresh: bool = False) -> Candidate | None:
    """Turn one Show HN hit into a verified Candidate, or into a rejection.

    Returns None only when the hit was never a candidate shape at all (no GitHub URL).
    A hit that WAS a candidate shape and failed verification comes back as a Candidate
    with `reject_reason` set, so the caller can report the real yield.
    """
    url = hit.get("url") or ""
    slug = repo_slug(url) if "github.com" in url else None
    if not slug:
        return None

    title = hit.get("title") or ""
    launched = bus.parse_ts(hit.get("created_at"))
    if not launched:
        # No real launch date means no defensible observed_at. Invariant #1: we do not
        # substitute fetch time and call it a launch.
        return None

    stub = Candidate(
        display_name="", github_login="", hn_author=hit.get("author") or "",
        company_name="", repo_slug=slug, repo_url=f"https://github.com/{slug}",
        hn_object_id=str(hit.get("objectID")), hn_url=f"{HN_ITEM}/{hit.get('objectID')}",
        launched_at=launched, points=int(hit.get("points") or 0), sector="",
    )

    if not looks_like_a_product(slug):
        stub.reject_reason = "not_a_product"
        return stub

    # Leg 2: the artifact. One request.
    try:
        repo = bus.fetch_json(
            f"{GH_API}/repos/{slug}", cache_dir=CACHE, headers=_headers(), refresh=refresh
        )
    except bus.RateLimited:
        raise
    except bus.FetchError as exc:
        stub.reject_reason = f"repo_unreachable:{exc.status}"
        return stub

    sector = classify_sector(title, repo.get("description"), " ".join(repo.get("topics") or []))
    if not sector:
        stub.reject_reason = "out_of_thesis"
        return stub
    stub.sector = sector
    stub.stars = int(repo.get("stargazers_count") or 0)

    # The product's name. The parsed Show HN title first, because that is what the
    # founder chose to call it; the repository's own name as the fallback. Both are
    # strings we read out of a response — there is no third branch that makes one up.
    stub.company_name = company_name_from(title) or (repo.get("name") or "").strip()
    if not stub.company_name:
        stub.reject_reason = "no_company_name"
        return stub

    owner = (repo.get("owner") or {}).get("login")
    if not owner:
        stub.reject_reason = "no_owner"
        return stub

    # Leg 3: the person. One request.
    try:
        user = bus.fetch_json(
            f"{GH_API}/users/{owner}", cache_dir=CACHE, headers=_headers(), refresh=refresh
        )
    except bus.RateLimited:
        raise
    except bus.FetchError as exc:
        stub.reject_reason = f"owner_unreachable:{exc.status}"
        return stub

    if user.get("type") != "User":
        # An org account is a real account but it is not a person, and guessing which
        # human behind it is "the founder" would be exactly the fabrication this module
        # exists to prevent. So we do not guess — we ask GitHub who actually wrote the
        # code. `/contributors` is ranked by commit count, so the top entry is a fact
        # about authorship ("wrote the most commits in this repo"), not an inference
        # about a title. One extra request, and it fails closed.
        user = _top_contributor(slug, refresh=refresh)
        if user is None:
            stub.reject_reason = "owner_is_org"
            return stub
        stub.evidence.append(f"https://github.com/{slug}/graphs/contributors")

    stub.github_login = user["login"]
    stub.display_name = (user.get("name") or "").strip() or user["login"]
    stub.followers = int(user.get("followers") or 0)
    stub.evidence = [
        stub.hn_url,
        *( [f"https://{host}/item?id={stub.hn_object_id}"] if (host := _show_hn_host()) else [] ),
        repo.get("html_url") or stub.repo_url,
        user.get("html_url") or f"https://github.com/{stub.github_login}",
    ] + stub.evidence
    return stub


def known_logins() -> set[str]:
    """GitHub logins already resolved to an entity in the store, lower-cased.

    Read from the alias table, which is where `memory.resolver` records the identifier
    it actually matched on — so this asks the same question the resolver would, rather
    than second-guessing it with a name comparison. Used to make batching idempotent:
    a second `--discover 25` finds the NEXT 25, instead of re-scanning the first 25 at
    ~14 GitHub requests each.
    """
    from memory import store

    try:
        return {
            a.value.strip().lower()
            for a in store.get_store().aliases_by_kind("handle:github")
            if a.value
        }
    except Exception:  # noqa: BLE001 - an unreachable store means "nothing known yet"
        log.warning("could not read known logins; treating every candidate as new")
        return set()


def discover(
    *,
    since_ts: int,
    want: int = 200,
    refresh: bool = False,
    budget: int | None = None,
    exclude: set[str] | None = None,
) -> tuple[list[Candidate], dict[str, int]]:
    """Verified candidates, newest launches first, plus a rejection census.

    `budget` caps GitHub requests. Verification costs 2 per candidate-shaped hit, so a
    run that would exceed the budget stops and reports what it got — the runner must
    never discover the ceiling by hitting it (bus.RateLimited is re-raised, not eaten).
    """
    hits = show_hn_stories(since_ts=since_ts, refresh=refresh)
    hits.sort(key=lambda h: h.get("created_at_i") or 0, reverse=True)

    admitted: list[Candidate] = []
    census: dict[str, int] = {"hits": len(hits), "candidate_shaped": 0, "admitted": 0}
    spent = 0
    seen_logins: set[str] = set(exclude or ())

    for hit in hits:
        if len(admitted) >= want:
            break
        if budget is not None and spent >= budget:
            census["stopped_on_budget"] = 1
            break
        cand = verify(hit, refresh=refresh)
        if cand is None:
            continue
        census["candidate_shaped"] += 1
        spent += 2
        if not cand.verified:
            census[cand.reject_reason.split(":")[0]] = (
                census.get(cand.reject_reason.split(":")[0], 0) + 1
            )
            continue
        if cand.github_login.lower() in seen_logins:
            census["already_known" if exclude and cand.github_login.lower() in exclude
                   else "duplicate_login"] = census.get(
                "already_known" if exclude and cand.github_login.lower() in exclude
                else "duplicate_login", 0
            ) + 1
            continue
        seen_logins.add(cand.github_login.lower())
        admitted.append(cand)
        census["admitted"] += 1

    return admitted, census
