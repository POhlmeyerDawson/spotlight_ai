"""web scanner. Owner: B. Emits RawSignal -> bus.ingest().

Enrichment, not a primary scanner: given a resolved name or handle, sweep for the
footprint the three APIs miss — personal sites, non-English blogs, regional dev
communities, conference talks. That footprint is disproportionately what a Type 6
founder has instead of an HN presence.

Tavily rarely carries a trustworthy publish date. When we can't extract a real one we
pass the earliest date we can actually defend (from the URL or the snippet) as a floor
and let the bus flag date_inferred. Never a silent now().

SCOPE DECISION — open discovery, on. `core.search.search()` defaults
`restrict_to_registry=True`, which clamps every query to the registry's 42 domains. This
scanner omitted the override, so a sweep for a Type 6 founder's personal site returned
medium.com and substack.com: the registry's blogging platforms, i.e. exactly the
English-language, platform-hosted footprint the docstring above says we are trying to get
PAST. Either the promise or the call had to go, and the promise is the point of the
scanner — a founder whose entire footprint is a self-hosted blog in Portuguese is
invisible to the other three scanners and the registry both.

So `scan()` passes `restrict_to_registry=False`, and pays the price the registry was
protecting: anything found OFF the registry is enrichment-only. It is marked
`enrichment_only` and carries reduced confidence, per `core/search.py` — discovery, never
evidence, until something promotes it to a real fetch. `sourcing/research.py` is that
promotion path; this scanner does not promote anything on its own.

IDENTITY GATE — why this scanner now refuses most of what it finds.

Open discovery searched the real web for founder NAMES and stored whatever came back as
`profile_fact`, which is an assertion that the page is ABOUT THAT PERSON. Nothing in this
module ever checked that. The only predicate on a result was "is it a URL I have not seen",
and the subject was then asserted by fiat in `_signal()`. Tavily's relevance score was
recorded and never compared to anything.

What that produced, in the live store: a German painter's cookie notice
(`renate-kohl-art.eu/privacy-policy`), a cargo-bike enthusiast's LinkedIn
(`linkedin.com/in/jordankohl`, relevance 0.087), a metaphysical-sound-therapy consultant
(`linkedin.com/in/adrian-voss-phd-...`), the Wikipedia page of an Indian film ACTRESS, and
a Danish professional CYCLIST — each filed as evidence about a founder who merely shares a
surname fragment. These are real, uninvolved private individuals. Storing a stranger's
LinkedIn profile as a dossier on someone else is a privacy failure before it is ever a
correctness one, and it is not fixed by lowering a confidence score.

Two things were wrong and both are fixed here:

  1. NO FLOOR. A 0.087 hit was admitted on the same terms as a 0.9 one. There is now a
     relevance floor (`MIN_RELEVANCE`).

  2. NO IDENTITY CHECK. Fixed by `_identifies_subject()`, which uses the thresholds in
     `memory/resolver.py` — the module that owns name matching for this codebase — rather
     than inventing a second, weaker notion of "same person" here. A bare substring is
     explicitly NOT sufficient: "Kohl" appearing somewhere on a page is how
     `en.wikipedia.org/wiki/Hannelore_Kohl` became founder evidence.

A result that fails the identity check is DROPPED, not stored at reduced confidence. This
scanner has exactly one output kind, `PROFILE_FACT`, which asserts something is true OF A
PERSON; there is no weaker kind for it to fall back to, and inventing one would only move
the strangers rather than remove them. Since the pages in question are private
individuals', "do not retain it" is also the only answer that actually addresses the
privacy half of the problem.

REMAINING LIMIT, STATED PLAINLY. A name check cannot distinguish two real people who
genuinely share a name, and it never will. It is a floor, not a proof — which is why
`identity_verified` is carried explicitly rather than implied, and why nothing here is
scoring-eligible on its own.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from core.search import SearchResult, allowed_domains, search
from memory.resolver import NAME_FLOOR, name_similarity, normalize_name, url_identity
from schema.events import EventKind, RawSignal, Source
from sourcing import bus

# What an off-registry find is worth before anything corroborates it. Non-zero because a
# self-hosted blog IS the signal for a Type 6 founder; well under 1.0 because nothing has
# verified it beyond a search engine agreeing the page exists.
OFF_REGISTRY_CONFIDENCE = 0.4

# A search engine's own confidence that the result answers the query. Below this the hit
# is not evidence of anything and is not stored at all. Calibrated against the live
# corpus: the strangers admitted by the unguarded scanner scored 0.087 (a cargo-bike
# LinkedIn), 0.254 and 0.271, while the genuine find for a real founder — Thomas Wolf's
# own LinkedIn post, Hugging Face — scored 0.627. 0.30 sits below every genuine hit in
# the corpus and above the clear misses. It is a floor, not a filter: plenty of strangers
# score well (an actress's Wikipedia page scored 0.944), which is why the identity check
# below exists and why this number alone was never going to be enough.
MIN_RELEVANCE = 0.30

# Handle-bearing hosts whose URL names ONE specific individual. `memory.resolver`
# already knows how to read these (`url_identity`), so a mismatch between the handle and
# the subject is a positive signal that the page belongs to somebody else — which is
# exactly the case for `linkedin.com/in/jordankohl` under subject "Renata Kohl".
_PERSON_HANDLE_KINDS = frozenset({"linkedin", "github", "twitter", "x", "medium"})

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


def scan(query: str, limit: int = 20, *, open_discovery: bool = True) -> list[RawSignal]:
    """Sweep for the footprint the API scanners miss. See the module docstring on scope.

    `open_discovery=False` puts the sweep back inside the registry allowlist — for a
    caller that needs every result to be scoring-eligible without re-checking each one.
    """
    per_query = max(2, limit // len(QUERY_TEMPLATES))
    registry = frozenset(d.lower() for d in allowed_domains())
    seen: set[str] = set()
    signals: list[RawSignal] = []
    for template in QUERY_TEMPLATES:
        results = search(
            template.format(q=query),
            max_results=per_query,
            restrict_to_registry=not open_discovery,
        )
        for result in results:
            if not result.url or result.url in seen:
                continue
            seen.add(result.url)
            # A hit the search engine itself is unsure about is not evidence about a
            # person. Dropped outright rather than stored at low confidence: the whole
            # failure mode here was a stranger's page persisting because "0.4 is low".
            if (result.score or 0.0) < MIN_RELEVANCE:
                continue
            # The page must name THIS person. A hit that cannot be tied to the subject is
            # somebody else's page and is not retained in any form. See the docstring.
            if not _identifies_subject(result, query):
                continue
            signals.append(_signal(result, query, template, registry))
            if len(signals) >= limit:
                return signals
    return signals


def _on_registry(url: str, registry: frozenset[str]) -> bool:
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower()
    host = host.split(":")[0].removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in registry)


def _identifies_subject(r: SearchResult, subject: str) -> bool:
    """Does this page plausibly name THIS person — by more than a shared fragment?

    Delegates every notion of "same name" to `memory.resolver`, which owns matching for
    this codebase and whose `NAME_FLOOR` is the threshold below which a name contributes
    nothing to a merge. Re-deriving a looser rule here is what produced the incident.

    Two ways to qualify, and one way to be positively disqualified:

      * the URL carries a person handle (`linkedin.com/in/<slug>`) that matches the
        subject's name -> qualified;
      * the URL carries a person handle that does NOT match -> DISQUALIFIED outright.
        The page names a specific someone, and it is not the subject. This is the
        `linkedin.com/in/jordankohl` case, and it is the only check that can catch it;
      * otherwise EVERY part of the subject's name must independently match some token in
        the title or snippet at `NAME_FLOOR`. Requiring every part — not just the
        surname, and not just a fuzzy run — is what separates "Renata Kohl" from
        "Hannelore Kohl" (forename absent) and from a page about the given name "Renata"
        (surname absent). A surname fragment is not an identity.
    """
    normalized = normalize_name(subject)
    parts = [p for p in normalized.split() if p]
    if not parts:
        return False

    identity = url_identity(r.url)
    if identity and identity[0] in _PERSON_HANDLE_KINDS:
        handle = normalize_name(re.sub(r"[-_]+", " ", identity[1]))
        # A trailing opaque id (`renata-kohl-602b61b4`) is part of the slug, not the name.
        handle = re.sub(r"\b[0-9a-f]{6,}\b", " ", handle).strip()
        return name_similarity(handle, normalized) >= NAME_FLOOR

    haystack = normalize_name(f"{r.title} {r.snippet}")
    if not haystack:
        return False
    tokens = set(haystack.split())
    if not tokens:
        return False
    # Every part, independently. A single strong hit on the surname is precisely the
    # "fuzzy substring" this check exists to reject.
    return all(
        any(name_similarity(part, token) >= NAME_FLOOR for token in tokens)
        for part in parts
    )


def _signal(r: SearchResult, subject: str, template: str, registry: frozenset[str]) -> RawSignal:
    observed_at = bus.parse_ts(r.published_at)
    off_registry = not _on_registry(r.url, registry)
    return RawSignal(
        source=Source.WEB,
        source_url=r.url,
        content=f"{r.title}\n{r.snippet}".strip(),
        meta={
            "kind": str(EventKind.PROFILE_FACT),
            "observed_at": observed_at,  # None -> bus falls back to the floor below
            "date_floor": None if observed_at else _date_floor(r),
            "subject": subject,
            # Carried explicitly, never implied: `scan()` admits nothing that failed the
            # check, so a stored event that lacks this flag predates the gate.
            "identity_verified": True,
            "query": template.format(q=subject),
            "self_published": r.self_published,  # weighs below independent sources
            "relevance": r.score,
            # Found outside the registry: real, and not yet evidence. See the docstring.
            "off_registry": off_registry,
            "enrichment_only": off_registry,
            **({"confidence": OFF_REGISTRY_CONFIDENCE} if off_registry else {}),
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
