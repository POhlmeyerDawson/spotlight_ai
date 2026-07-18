"""arxiv scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.

Queries cs.LG, cs.DC, and cs.PL categories for AI infrastructure topics.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from schema.events import RawSignal, Source

CACHE_DIR = Path("data/raw/arxiv")

# arXiv API constants
ARXIV_API_URL = "http://export.arxiv.org/api/query"
CATEGORIES = ["cs.LG", "cs.DC", "cs.PL"]  # Machine Learning, Distributed Computing, Programming Languages

# Rate limiting for arXiv API
RATE_LIMIT_DELAY = 3.0  # arXiv recommends 3 seconds between requests


def _cache_path(query: str, start: int = 0) -> Path:
    """Create a cache file path for a given query and start index."""
    sanitized = "".join(c if c.isalnum() else "_" for c in query)[:80]
    return CACHE_DIR / f"{sanitized}_start_{start}.json"


def _load_cached(query: str, start: int = 0) -> dict | None:
    """Load cached response if it exists."""
    cache_file = _cache_path(query, start)
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    return None


def _save_cached(query: str, data: dict, start: int = 0) -> None:
    """Save response to cache."""
    cache_file = _cache_path(query, start)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data))


def _parse_arxiv_date(date_str: str) -> datetime:
    """Parse arXiv date format (YYYY-MM-DDTHH:MM:SSZ)."""
    if date_str is None:
        return datetime.now(timezone.utc)
    try:
        # arXiv uses ISO format with T separator
        if date_str.endswith("Z"):
            date_str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(date_str.replace("T", " ").replace("+00:00", "Z+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _rate_limited_request(url: str, params: dict) -> str:
    """Make a rate-limited request to arXiv API."""
    time.sleep(RATE_LIMIT_DELAY)
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.text


def _parse_entry(entry: dict) -> dict:
    """Parse an arXiv entry into a normalized format."""
    # Extract basic info
    title = entry.get("title", "").strip()
    summary = entry.get("summary", "").strip()

    # Extract authors
    authors = []
    for author in entry.get("authors", []):
        authors.append({
            "name": author.get("name", ""),
            "affiliation": author.get("arxiv_affiliation", ""),
        })

    # Extract categories
    categories = []
    for category in entry.get("categories", []):
        categories.append(category.get("term", ""))

    # Extract links
    links = []
    for link in entry.get("links", []):
        links.append({
            "href": link.get("href", ""),
            "rel": link.get("rel", ""),
            "type": link.get("type", ""),
        })

    # Extract published/updated dates
    published = entry.get("published", "")
    updated = entry.get("updated", "")

    # Extract arXiv ID and DOI
    arxiv_id = entry.get("id", "").split("/")[-1]
    doi = entry.get("doi", "")

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "summary": summary,
        "authors": authors,
        "categories": categories,
        "links": links,
        "published": published,
        "updated": updated,
        "doi": doi,
    }


def scan(query: str, limit: int = 50, categories: list[str] | None = None) -> list[RawSignal]:
    """Scan arXiv for papers matching the query.

    Args:
        query: Search query (e.g., "inference", "vector database", "compiler")
        limit: Maximum number of results to return
        categories: List of arXiv categories to search. Defaults to cs.LG, cs.DC, cs.PL

    Returns:
        List of RawSignal objects ready for bus.ingest()
    """
    if categories is None:
        categories = CATEGORIES

    raw_signals = []
    seen_ids = set()
    start = 0

    while len(raw_signals) < limit:
        search_query = f"search_query=all:{query}&start={start}&max_results=100"

        # Check cache
        cached = _load_cached(query, start)
        if cached is None:
            params = {
                "search_query": f"all:{query}",
                "start": start,
                "max_results": 100,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }

            try:
                xml_response = _rate_limited_request(ARXIV_API_URL, params)
                # Simple XML parsing - extract relevant fields
                cached = _parse_arxiv_xml(xml_response)
                _save_cached(query, cached, start)
            except Exception as e:
                print(f"Error fetching arXiv page {start}: {e}")
                break

        entries = cached.get("entries", [])
        if not entries:
            break

        for entry in entries:
            if len(raw_signals) >= limit:
                break

            arxiv_id = entry.get("arxiv_id", "")
            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)

            # Parse dates
            published = entry.get("published", "")
            observed_at = _parse_arxiv_date(published)

            # Build payload
            payload = {
                "arxiv_id": arxiv_id,
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "authors": entry.get("authors", []),
                "categories": entry.get("categories", []),
                "links": entry.get("links", []),
                "published": published,
                "updated": entry.get("updated", ""),
                "doi": entry.get("doi", ""),
            }

            raw_signal = RawSignal(
                source=Source.ARXIV,
                source_url=f"https://arxiv.org/abs/{arxiv_id}",
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={
                    "arxiv_id": arxiv_id,
                    "categories": entry.get("categories", []),
                    "author_names": [a.get("name", "") for a in entry.get("authors", [])],
                },
            )
            raw_signals.append(raw_signal)

        start += 100
        time.sleep(RATE_LIMIT_DELAY)

    return raw_signals[:limit]


def _parse_arxiv_xml(xml_text: str) -> dict:
    """Parse arXiv XML response into a dict."""
    import xml.etree.ElementTree as ET

    # Register namespaces
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    entries = []

    try:
        root = ET.fromstring(xml_text)

        # Find all entry elements
        for entry_elem in root.findall("atom:entry", namespaces):
            entry = {}

            # Title
            title_elem = entry_elem.find("atom:title", namespaces)
            entry["title"] = title_elem.text.strip() if title_elem is not None else ""

            # Summary
            summary_elem = entry_elem.find("atom:summary", namespaces)
            entry["summary"] = summary_elem.text.strip() if summary_elem is not None else ""

            # ID
            id_elem = entry_elem.find("atom:id", namespaces)
            entry["id"] = id_elem.text if id_elem is not None else ""

            # Extract arXiv ID from full URL
            if id_elem is not None:
                arxiv_id = id_elem.text.split("/")[-1]
                entry["arxiv_id"] = arxiv_id

            # Published date
            published_elem = entry_elem.find("atom:published", namespaces)
            entry["published"] = published_elem.text if published_elem is not None else ""

            # Updated date
            updated_elem = entry_elem.find("atom:updated", namespaces)
            entry["updated"] = updated_elem.text if updated_elem is not None else ""

            # Authors
            authors = []
            for author_elem in entry_elem.findall("atom:author", namespaces):
                author = {}
                name_elem = author_elem.find("atom:name", namespaces)
                author["name"] = name_elem.text if name_elem is not None else ""

                # arXiv affiliation
                affil_elem = author_elem.find("arxiv:affiliation", namespaces)
                author["arxiv_affiliation"] = affil_elem.text if affil_elem is not None else ""
                authors.append(author)
            entry["authors"] = authors

            # Categories (tags)
            categories = []
            for category_elem in entry_elem.findall("atom:category", namespaces):
                term = category_elem.get("term", "")
                categories.append(term)
            entry["categories"] = categories

            # Links
            links = []
            for link_elem in entry_elem.findall("atom:link", namespaces):
                link = {
                    "href": link_elem.get("href", ""),
                    "rel": link_elem.get("rel", ""),
                    "type": link_elem.get("type", ""),
                }
                links.append(link)
            entry["links"] = links

            # DOI
            doi_elem = entry_elem.find("arxiv:doi", namespaces)
            entry["doi"] = doi_elem.text if doi_elem is not None else ""

            entries.append(entry)

    except ET.ParseError as e:
        print(f"Error parsing arXiv XML: {e}")
        return {"entries": [], "total_results": 0}

    # Get total results
    total_elem = root.find("atom:totalResults", namespaces)
    total_results = int(total_elem.text) if total_elem is not None else 0

    return {
        "entries": entries,
        "total_results": total_results,
    }


def scan_by_author(author: str, limit: int = 50) -> list[RawSignal]:
    """Scan papers by a specific author.

    Args:
        author: Author name to search for
        limit: Maximum number of papers to return

    Returns:
        List of RawSignal objects
    """
    query = f"au:{author}"
    return scan(query, limit)


def scan_by_category(category: str, query: str = "", limit: int = 50) -> list[RawSignal]:
    """Scan papers in a specific category.

    Args:
        category: arXiv category (cs.LG, cs.DC, cs.PL, etc.)
        query: Optional search query within the category
        limit: Maximum number of papers to return

    Returns:
        List of RawSignal objects
    """
    if query:
        full_query = f"cat:{category} AND all:{query}"
    else:
        full_query = f"cat:{category}"

    return scan(full_query, limit, [category])


def scan_recent(category: str, limit: int = 50) -> list[RawSignal]:
    """Scan recent papers in a specific category.

    Args:
        category: arXiv category
        limit: Maximum number of papers to return

    Returns:
        List of RawSignal objects
    """
    # For recent papers, sort by submission date (descending)
    raw_signals = []

    # Fetch in batches
    for start in range(0, limit, 100):
        batch_limit = min(100, limit - len(raw_signals))
        batch_query = f"cat:{category}&start={start}&max_results={batch_limit}&sortBy=submittedDate&sortOrder=descending"
        batch_signals = scan(batch_query, batch_limit, [category])
        raw_signals.extend(batch_signals)

        if len(batch_signals) < batch_limit:
            break

        time.sleep(RATE_LIMIT_DELAY)

    return raw_signals[:limit]
