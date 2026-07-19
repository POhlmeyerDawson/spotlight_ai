"""Tavily — the independent source behind C's VERIFIED/CONTRADICTED verdicts.

Two things that make this defensible rather than decorative:
  1. Every result keeps its URL + snippet, so a verdict can cite something.
  2. Results are UNTRUSTED (a founder can plant a page) — callers must route
     snippets through llm.complete(untrusted=...), never raw into a prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from core.config import settings

CACHE_DIR = Path("data/raw/tavily")

# Domains where a founder controls the content. Corroboration from here is weak.
SELF_PUBLISHED_HINTS = ("linkedin.com", "medium.com", "substack.com", "twitter.com", "x.com")


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    score: float = 0.0
    published_at: str | None = None  # often absent — see B.md on date_inferred
    self_published: bool = False


def _registry() -> dict:
    path = Path("data/sources.json")
    if not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return blob if isinstance(blob, dict) else {}


def _enabled_sources() -> list[dict]:
    return [
        s
        for s in (_registry().get("sources") or [])
        if isinstance(s, dict) and s.get("enabled", True)
    ]


def _domains_of(src: dict) -> list[str]:
    return [
        d.strip()
        for d in (src.get("include_domains") or src.get("domains") or [])
        if isinstance(d, str) and d.strip()
    ]


# ---------------------------------------------------------------------------
# Sector resolution.
#
# The allowlist used to be ONE GLOBAL LIST: every enabled source's domains,
# concatenated, regardless of what the fund actually invests in. With the shipped
# registry that was 42 domains, 38 of them code or CS-research, so a biotech
# founder was not scored down — they were structurally unreadable, which lands in
# the same place under a min-axis ranking policy and is a far worse error because
# it is invisible. The allowlist now resolves against the ACTIVE THESIS.
#
# Failing OPEN is deliberate everywhere below. An unreadable registry, a thesis
# with no sectors, or a source with no `sectors` key all resolve to "allowed",
# because a filter that silently empties the allowlist produces output identical
# to "this founder has no footprint" and nobody would ever notice the difference.
# ---------------------------------------------------------------------------

_ANY = "*"


def _norm_sector(value: object) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


def _sector_families() -> dict[str, frozenset[str]]:
    """family -> the thesis sector ids that belong to it, from the registry."""
    raw = _registry().get("sector_families") or {}
    return {
        _norm_sector(family): frozenset(_norm_sector(m) for m in members)
        for family, members in raw.items()
        if isinstance(members, list) and not str(family).startswith("_")
    }


def active_sectors(thesis: dict | None = None) -> frozenset[str]:
    """Normalised sector ids from the active thesis. Empty means 'no restriction'."""
    from core import thesis as thesis_mod

    try:
        return frozenset(thesis_mod.included_sectors(thesis))
    except Exception:  # noqa: BLE001 — an unreadable thesis must not empty the allowlist
        return frozenset()


def _source_serves(src: dict, sectors: frozenset[str]) -> bool:
    """Does this source serve any sector the thesis is actually looking at?

    A source with no `sectors` key serves everything: an addition to the registry
    that forgot the tag stays reachable rather than vanishing from every search.
    """
    if not sectors:
        return True
    tags = src.get("sectors")
    if not isinstance(tags, list) or not tags:
        return True
    families = _sector_families()
    for tag in tags:
        t = _norm_sector(tag)
        if t == _ANY:
            return True
        if t in sectors:
            return True
        if families.get(t, frozenset()) & sectors:
            return True
    return False


def sources_for_thesis(thesis: dict | None = None) -> list[dict]:
    """Enabled registry sources that serve the active thesis's sectors."""
    sectors = active_sectors(thesis)
    return [src for src in _enabled_sources() if _source_serves(src, sectors)]


def allowed_domains(thesis: dict | None = None) -> list[str]:
    """Domains the source registry permits FOR THE ACTIVE THESIS.

    Empty means unrestricted — a missing or unreadable registry must not silently
    filter every search to nothing, which would look identical to "the founder has
    no footprint". Tavily accepts up to 300 include_domains.
    """
    out: list[str] = []
    for src in sources_for_thesis(thesis):
        out.extend(_domains_of(src))
    return out[:300]


def source_domains(source_id: str) -> list[str]:
    """Domains of one ENABLED registry source that SERVES THE ACTIVE THESIS.

    A caller wanting to search a single source asks for it by registry id rather than
    typing a domain, so a source that is disabled in the registry cannot be reached by
    a hard-coded string somewhere in `sourcing/`. Sector scoping is applied here for
    the same reason: `search()` rejects anything outside the allowlist, so returning
    an out-of-thesis source's domains would only produce a raise at the call site.
    """
    for src in sources_for_thesis():
        if src.get("id") == source_id:
            return _domains_of(src)
    return []


def thesis_serves_family(family: str, thesis: dict | None = None) -> bool:
    """Does the active thesis include any sector belonging to `family`?

    Used by the two rules in `intelligence/flags.py` whose keyword lists are
    SECTOR-LITERAL rather than sector-neutral — `infra_domain_depth` matches
    "compiler|kernel|gpu|quantization", `benchmarks_published` matches
    "benchmark|latency|throughput". Those describe depth in software specifically,
    so leaving them applicable under a biotech or fintech thesis meant every founder
    in that thesis was silently marked down for not using software vocabulary.

    Answers TRUE for a thesis that names no sector at all, so an unconfigured
    installation behaves exactly as it did before.
    """
    sectors = active_sectors(thesis)
    if not sectors:
        return True
    fam = _norm_sector(family)
    return bool(_sector_families().get(fam, frozenset()) & sectors) or fam in sectors


def evidence_classes() -> list[dict]:
    """The evidence-type equivalence table from `data/sources.json`.

    "Shipped a release" is one INSTANCE of "put a working thing in front of people
    who did not have to be nice about it". A registered clinical trial, a granted
    licence and a deposited dataset are others. The classes live in config so that
    adding an industry is a config change; `intelligence/flags.py` turns the
    instances that match the active thesis into rules.
    """
    block = _registry().get("evidence_classes") or {}
    classes = block.get("classes")
    return [c for c in classes if isinstance(c, dict)] if isinstance(classes, list) else []


def instance_channel(instance: dict) -> tuple[frozenset[str], frozenset[str]]:
    """(registry source ids, domains) an evidence instance may be evidenced from.

    Returned as BOTH because an ingested event may identify its origin either by
    `payload["source_id"]` (set by `sourcing/research.py::_source_id_for`) or only by
    its URL. Matching on either is what stops a real filing from being invisible
    because one metadata field was not populated.
    """
    ids = frozenset(str(s) for s in (instance.get("sources") or []) if isinstance(s, str))
    domains: set[str] = set()
    for src in _enabled_sources():
        if str(src.get("id")) in ids:
            domains.update(d.lower() for d in _domains_of(src))
    return ids, frozenset(domains)


def instances_for_sectors(sectors: frozenset[str]) -> list[tuple[dict, dict]]:
    """(class, instance) pairs whose declared sectors intersect `sectors`.

    Empty `sectors` yields nothing rather than everything: a thesis that names no
    sector is "look at all companies", not "evaluate every founder against every
    industry's evidence at once".
    """
    if not sectors:
        return []
    families = _sector_families()
    out: list[tuple[dict, dict]] = []
    for cls in evidence_classes():
        for inst in cls.get("instances") or []:
            if not isinstance(inst, dict):
                continue
            tags = {_norm_sector(t) for t in (inst.get("sectors") or [])}
            hit = _ANY in tags or bool(tags & sectors)
            if not hit:
                hit = any(families.get(t, frozenset()) & sectors for t in tags)
            if hit:
                out.append((cls, inst))
    return out


def registry_coverage(thesis: dict | None = None) -> dict:
    """How well this registry can actually SEE the sectors the thesis invests in.

    This is the product-facing half of the absence discipline. `data/sources.json`
    has always carried `coverage_gaps` describing exactly whom it misses, and that
    text sat in a config file where no user of the system ever saw it. A thin
    dossier therefore read as "this founder is weak" when the honest reading was
    "we could not see them". This function is what lets the output say the second
    thing.

    `well_covered` is deliberately conservative: a sector counts as covered only
    when a source at the registry's top two trust levels is tagged for it
    specifically (see the `tiers` block in the registry), not merely when
    some `*`-tagged catch-all would return something. Tavily enrichment and a
    Substack search will always return SOMETHING for any sector, and treating that
    as coverage is precisely how a system convinces itself it can see.
    """
    sectors = active_sectors(thesis)
    serving = sources_for_thesis(thesis)
    per_sector: dict[str, dict] = {}
    for sector in sorted(sectors):
        specific = [
            src
            for src in serving
            if _source_serves(src, frozenset({sector}))
            and _ANY not in {_norm_sector(t) for t in (src.get("sectors") or [])}
            and isinstance(src.get("tier"), int)
            and src["tier"] <= 2
        ]
        per_sector[sector] = {
            "dedicated_sources": sorted(str(s.get("id")) for s in specific),
            "n_dedicated_sources": len(specific),
            "well_covered": len(specific) >= 2,
        }
    thin = sorted(s for s, v in per_sector.items() if not v["well_covered"])
    return {
        "sectors": sorted(sectors),
        "n_sources": len(serving),
        "n_domains": len(allowed_domains(thesis)),
        "per_sector": per_sector,
        "thinly_covered_sectors": thin,
        "known_gaps": [
            gap.get("who")
            for gap in ((_registry().get("coverage_gaps") or {}).get("misses") or [])
            if isinstance(gap, dict) and gap.get("severity") in {"high", "medium"}
        ],
        "caveat": (
            "Absence of evidence in a thinly covered sector is a fact about this "
            "registry, not about the founder. Read a low evidence count here as "
            "'we could not see them', never as 'they are weak'."
        )
        if thin
        else None,
    }


def corroboration_only_domains() -> frozenset[str]:
    """Domains of sources the registry marks `scoring_eligible: false`.

    A source lands here when its coverage is a PR-budget and prior-visibility artifact —
    i.e. the same term `hidden_ranking` subtracts — but its reporting is still an
    independent check on a claim the founder made. Verification does not ADD score, it
    only removes doubt, so these domains are legitimate for `intelligence/validator.py`
    and are structurally barred from the evidence path in `sourcing/research.py`.

    The default is scoring-eligible: a source has to opt OUT explicitly, so nothing that
    exists today changes behaviour.

    NOT sector-scoped, deliberately, and this is the one place that asymmetry matters:
    this set is a PROHIBITION, and a prohibition that narrows with the thesis would mean
    a domain barred from scoring under one thesis becomes scoreable under another. The
    allowlist narrows; the ban does not.
    """
    out: set[str] = set()
    for src in _enabled_sources():
        if src.get("scoring_eligible", True) is False:
            out.update(d.lower() for d in _domains_of(src))
    return frozenset(out)


def is_corroboration_only(url: str) -> bool:
    """Is this URL from a source that may corroborate but may never score?"""
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower()
    host = host.split(":")[0].removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in corroboration_only_domains())


def search(
    query: str,
    *,
    max_results: int = 5,
    days: int | None = None,
    restrict_to_registry: bool = True,
    only_domains: list[str] | None = None,
) -> list[SearchResult]:
    """Cached web search. Empty results mean UNVERIFIABLE, never CONTRADICTED.

    Restricted to the registry's domains by default, resolved AGAINST THE ACTIVE
    THESIS (see `allowed_domains`). Without the passthrough the registry was
    decorative: every source was tiered, weighted and reasoned about, and the actual
    query still went to the open web. Pass restrict_to_registry=False for genuinely
    open discovery, and remember that anything it finds is enrichment-only until
    promoted to a real fetch.

    `only_domains` NARROWS the search to one registry source (see `source_domains`). It
    can never widen it: anything outside the current allowlist raises. A caller cannot
    reach a domain the registry disabled by naming it here, which is what stops
    "search just this one site" from becoming a private, unaudited source list.
    """
    domains = allowed_domains() if restrict_to_registry else []
    if only_domains is not None:
        requested = [d.strip() for d in only_domains if isinstance(d, str) and d.strip()]
        outside = sorted(set(requested) - set(allowed_domains()))
        if outside or not requested:
            raise ValueError(
                f"only_domains must be a non-empty subset of the registry's enabled "
                f"domains; {outside or 'nothing'} is not one of them. Add the source to "
                "data/sources.json rather than passing a domain here."
            )
        domains = requested
    key = "".join(c if c.isalnum() else "_" for c in query)[:80]
    # Domain set participates in the cache key: the same query against a different
    # allowlist is a different query, and reusing the answer would silently serve
    # results from sources the registry has since disabled.
    scope = "all" if not domains else str(abs(hash(tuple(sorted(domains)))) % 10**8)
    cache_file = CACHE_DIR / f"{key}_{max_results}_{scope}.json"
    if cache_file.exists():
        return [SearchResult(**r) for r in json.loads(cache_file.read_text())]

    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.tavily_api_key)
    raw = client.search(
        query=query,
        max_results=max_results,
        search_depth="advanced",
        **({"days": days} if days else {}),
        **({"include_domains": domains} if domains else {}),
    )

    results = [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", ""),
            score=r.get("score", 0.0),
            published_at=r.get("published_date"),
            self_published=_is_self_published(r.get("url", "")),
        )
        for r in raw.get("results", [])
    ]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps([r.model_dump() for r in results]))
    return results


def _is_self_published(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in SELF_PUBLISHED_HINTS)


def is_self_published(url: str | None) -> bool:
    """Is this URL a surface the founder controls end to end?

    Public because it is no longer only a search-result annotation: it is half of
    the corroboration reading `intelligence/flags.py` attaches to a rollup and
    `memory/score.py` prices. Self-published is a property of WHO CONTROLS THE PAGE,
    which is orthogonal to whether the artifact is code — which is the whole reason
    the old `web 1.0 / deck 2.0` ordering had to go.
    """
    return _is_self_published(url) if url else False
