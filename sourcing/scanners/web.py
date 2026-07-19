"""web scanner. Owner: B. Emits RawSignal -> bus.ingest().

Enrichment, not a primary scanner: given a resolved name or handle, sweep for the
footprint the three APIs miss — personal sites, non-English blogs, regional dev
communities, conference talks. That footprint is disproportionately what a Type 6
founder has instead of an HN presence.

Tavily rarely carries a trustworthy publish date. When we can't extract a real one we
pass the earliest date we can actually defend (from the URL or the snippet) as a floor
and let the bus flag date_inferred. Never a silent now().
"""

from __future__ import annotations

import re

from core.search import SearchResult, search
from schema.events import EventKind, RawSignal, Source
from sourcing import bus

# core.search already caches raw Tavily responses under data/raw/tavily/.

# The bare-quoted query goes first on purpose: adding English qualifiers is exactly what
# buries a non-English footprint.
QUERY_TEMPLATES = (
    '"{q}"',
    "{q} personal site blog",
    "{q} open source project author",
    "{q} conference talk OR meetup OR workshop",
)

# Handles /2020/11/14/ and /2020/Nov/14/ alike — the second form is common on blogs.
_URL_DATE_RE = re.compile(r"/(20\d{2})[/-](\d{1,2}|[A-Za-z]{3,9})(?:[/-](\d{1,2}))?/")
_TEXT_DATE_RE = re.compile(
    r"\b(?:(20\d{2})-(\d{1,2})-(\d{1,2})"
    r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2}))\b",
    re.IGNORECASE,
)
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def scan(query: str, limit: int = 20) -> list[RawSignal]:
    per_query = max(2, limit // len(QUERY_TEMPLATES))
    seen: set[str] = set()
    signals: list[RawSignal] = []
    for template in QUERY_TEMPLATES:
        for result in search(template.format(q=query), max_results=per_query):
            if not result.url or result.url in seen:
                continue
            seen.add(result.url)
            signals.append(_signal(result, query, template))
            if len(signals) >= limit:
                return signals
    return signals


def _signal(r: SearchResult, subject: str, template: str) -> RawSignal:
    observed_at = bus.parse_ts(r.published_at)
    return RawSignal(
        source=Source.WEB,
        source_url=r.url,
        content=f"{r.title}\n{r.snippet}".strip(),
        meta={
            "kind": str(EventKind.PROFILE_FACT),
            "observed_at": observed_at,  # None -> bus falls back to the floor below
            "date_floor": None if observed_at else _date_floor(r),
            "subject": subject,
            "query": template.format(q=subject),
            "self_published": r.self_published,  # weighs below independent sources
            "relevance": r.score,
            "evidence_span": (r.snippet or r.title)[:200],
        },
    )


def _date_floor(r: SearchResult):
    """Earliest date we can point at. A date in the URL path is the most reliable of these."""
    if m := _URL_DATE_RE.search(r.url):
        month = _MONTHS.get(m[2][:3].lower()) if m[2].isalpha() else int(m[2])
        if month and 1 <= month <= 12:
            return f"{m[1]}-{month:02d}-{int(m[3] or 1):02d}"
    if m := _TEXT_DATE_RE.search(f"{r.title} {r.snippet}"):
        if m[1]:
            return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
        return f"{m[6]}-{_MONTHS[m[4][:3].lower()]:02d}-{int(m[5]):02d}"
    return None
