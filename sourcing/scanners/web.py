"""web scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.

This is the enrichment channel - not a primary scanner. Given a resolved name/handle,
sweep for footprint the three APIs miss: personal sites, non-English blogs, regional
dev communities, conference talks.

This is disproportionately valuable for Type 6 - the invisible-international founder
whose work isn't on HN.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schema.events import RawSignal, Source

from core.config import settings
from core.search import search as tavily_search

CACHE_DIR = Path("data/raw/web")


def _cache_path(query: str) -> Path:
    """Create a cache file path for a given query."""
    sanitized = "".join(c if c.isalnum() else "_" for c in query)[:80]
    return CACHE_DIR / f"{sanitized}.json"


def _load_cached(query: str) -> dict | None:
    """Load cached response if it exists."""
    cache_file = _cache_path(query)
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    return None


def _save_cached(query: str, data: dict) -> None:
    """Save response to cache."""
    cache_file = _cache_path(query)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data))


def _parse_date_from_snippet(snippet: str) -> datetime | None:
    """Try to extract a date from a snippet."""
    import re
    from datetime import datetime

    # Common date patterns
    patterns = [
        r"(\d{4})-(\d{2})-(\d{2})",  # 2024-01-15
        r"(\d{2})/(\d{2})/(\d{4})",  # 01/15/2024
        r"(\d{4})/(\d{2})/(\d{2})",  # 2024/01/15
        r"(\w+)\s+(\d{1,2}),\s+(\d{4})",  # January 15, 2024
        r"(\d{1,2})\s+(\w+)\s+(\d{4})",  # 15 January 2024
    ]

    for pattern in patterns:
        match = re.search(pattern, snippet)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 3:
                    # Handle different formats
                    if groups[0].isdigit() and len(groups[0]) == 4:
                        # YYYY-MM-DD or YYYY/MM/DD
                        return datetime(int(groups[0]), int(groups[1]), int(groups[2]), tzinfo=timezone.utc)
                    elif groups[0].isdigit():
                        # MM/DD/YYYY or DD/MM/YYYY
                        return datetime(int(groups[2]), int(groups[1]), int(groups[0]), tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue

    return None


def scan(handle: str, search_terms: list[str] | None = None, limit: int = 20) -> list[RawSignal]:
    """Scan the web for a person's footprint using their handle/name.

    This enriches data from the primary scanners (HN, GitHub, arXiv) by finding:
    - Personal sites and blogs
    - Non-English content
    - Regional developer communities
    - Conference talks and presentations
    - Podcast appearances
    - Interview mentions

    Args:
        handle: GitHub username, HN username, or full name to search for
        search_terms: Optional additional terms to refine search
        limit: Maximum number of results to return

    Returns:
        List of RawSignal objects ready for bus.ingest()
    """
    if not settings.tavily_api_key:
        print("TAVILY_API_KEY not set. Skipping web scan.")
        return []

    raw_signals = []
    seen_urls = set()

    # Build search queries
    queries = [
        f"{handle} personal website",
        f"{handle} blog",
        f"{handle} medium",
        f"{handle} substack",
        f"{handle} twitter",
        f"{handle} linkedin",
        f"{handle} conference talk",
        f"{handle} podcast",
        f"{handle} interview",
        f"{handle} youtube",
        f"{handle} github",
        f"{handle} arxiv",
    ]

    if search_terms:
        for term in search_terms:
            queries.append(f"{handle} {term}")

    for query in queries:
        # Check cache
        cached = _load_cached(query)
        if cached is None:
            try:
                results = tavily_search(query, max_results=10)
                cached = {
                    "query": query,
                    "results": [r.model_dump() if hasattr(r, "model_dump") else r for r in results],
                }
                _save_cached(query, cached)
            except Exception as e:
                print(f"Error searching for '{query}': {e}")
                continue

            # Rate limiting
            time.sleep(0.5)

        for result in cached.get("results", []):
            if len(raw_signals) >= limit:
                break

            url = result.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = result.get("title", "")
            snippet = result.get("snippet", "")

            # Try to extract date from snippet or published_date
            published_at = result.get("published_at")
            observed_at = None

            if published_at:
                try:
                    observed_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                except ValueError:
                    observed_at = None

            if observed_at is None:
                observed_at = _parse_date_from_snippet(snippet)

            # If still no real date, set to earliest defensible date and flag it
            if observed_at is None:
                observed_at = datetime(2010, 1, 1, tzinfo=timezone.utc)  # Earliest reasonable date
                integrity_flags = ["date_inferred"]
            else:
                integrity_flags = []

            payload = {
                "url": url,
                "title": title,
                "snippet": snippet,
                "source_type": _categorize_source(url),
            }

            raw_signal = RawSignal(
                source=Source.WEB,
                source_url=url,
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={
                    "search_query": query,
                    "handle": handle,
                },
                integrity_flags=integrity_flags,
            )
            raw_signals.append(raw_signal)

    return raw_signals[:limit]


def _categorize_source(url: str) -> str:
    """Categorize a URL into a source type."""
    url_lower = url.lower()

    if "medium.com" in url_lower or "medium.com" in url_lower:
        return "blog"
    if "substack.com" in url_lower:
        return "newsletter"
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "social"
    if "linkedin.com" in url_lower:
        return "professional"
    if "github.com" in url_lower:
        return "code"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "video"
    if "vimeo.com" in url_lower:
        return "video"
    if "conf" in url_lower or "conference" in url_lower or "talk" in url_lower:
        return "conference"
    if "podcast" in url_lower or "spotify.com" in url_lower:
        return "podcast"
    if "interview" in url_lower:
        return "interview"
    if "arxiv.org" in url_lower:
        return "paper"
    if "hn.algolia.com" in url_lower or "news.ycombinator.com" in url_lower:
        return "forum"

    return "article"


def scan_by_name(name: str, location: str | None = None, limit: int = 20) -> list[RawSignal]:
    """Scan the web for someone by name (useful for non-English names).

    Args:
        name: Full name to search for
        location: Optional location to narrow search
        limit: Maximum number of results

    Returns:
        List of RawSignal objects
    """
    search_terms = [name]

    if location:
        search_terms.append(f"{name} {location}")

    # Also try just the first name
    parts = name.split()
    if len(parts) > 1:
        search_terms.append(f"{parts[0]} {parts[-1]}")

    raw_signals = []
    seen_urls = set()

    for term in search_terms:
        signals = scan(term, limit=limit - len(raw_signals))
        for signal in signals:
            if signal.source_url not in seen_urls:
                seen_urls.add(signal.source_url)
                raw_signals.append(signal)

    return raw_signals[:limit]


def scan_for_footprint(handles: dict[str, str], limit: int = 30) -> list[RawSignal]:
    """Scan for a person's footprint across multiple platforms.

    Args:
        handles: Dict mapping platform names to handles, e.g., {"github": "username", "hn": "username"}
        limit: Total maximum results

    Returns:
        List of RawSignal objects
    """
    raw_signals = []
    seen_urls = set()

    # Get primary handle (GitHub or HN)
    primary = handles.get("github") or handles.get("hn") or handles.get("name", "")

    # Scan for general footprint
    signals = scan(primary, limit=limit)
    for signal in signals:
        if signal.source_url not in seen_urls:
            seen_urls.add(signal.source_url)
            raw_signals.append(signal)

    return raw_signals[:limit]


def scan_conference_talks(handle: str, limit: int = 10) -> list[RawSignal]:
    """Specifically search for conference talks and presentations.

    Args:
        handle: Person's handle to search for
        limit: Maximum number of talks to return

    Returns:
        List of RawSignal objects
    """
    search_terms = [
        f"{handle} conference talk",
        f"{handle} presentation",
        f"{handle} meetup",
        f"{handle} lightning talk",
        f"{handle} workshop",
        f"{handle} developer conference",
    ]

    raw_signals = []
    seen_urls = set()

    for term in search_terms:
        signals = scan(term, limit=limit - len(raw_signals))
        for signal in signals:
            if signal.source_url not in seen_urls:
                seen_urls.add(signal.source_url)
                signal.meta["content_type"] = "talk"
                raw_signals.append(signal)

    return raw_signals[:limit]


def scan_non_english(handle: str, languages: list[str] | None = None, limit: int = 10) -> list[RawSignal]:
    """Search for content in non-English languages.

    Args:
        handle: Person's handle to search for
        languages: List of language codes (e.g., "lang_de", "lang_jp", "lang_zh")
        limit: Maximum number of results per language

    Returns:
        List of RawSignal objects
    """
    if languages is None:
        languages = ["lang_de", "lang_jp", "lang_zh", "lang_ko", "lang_es", "lang_fr", "lang_pt"]

    raw_signals = []
    seen_urls = set()

    for lang in languages:
        term = f"{handle} {lang}"
        signals = scan(term, limit=limit - len(raw_signals))
        for signal in signals:
            if signal.source_url not in seen_urls:
                seen_urls.add(signal.source_url)
                signal.meta["language"] = lang
                signal.meta["content_type"] = "non_english"
                raw_signals.append(signal)

    return raw_signals[:limit]
