"""hn scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from schema.events import RawSignal, Source

from core.config import settings

CACHE_DIR = Path("data/raw/hn")
RATE_LIMIT_DELAY = 1.0  # seconds between requests to avoid rate limiting


def _cache_path(query: str, page: int = 0) -> Path:
    """Create a cache file path for a given query and page."""
    # Sanitize query for filename
    sanitized = "".join(c if c.isalnum() else "_" for c in query)[:80]
    return CACHE_DIR / f"{sanitized}_page_{page}.json"


def _load_cached(query: str, page: int = 0) -> dict | None:
    """Load cached response if it exists."""
    cache_file = _cache_path(query, page)
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    return None


def _save_cached(query: str, data: dict, page: int = 0) -> None:
    """Save response to cache."""
    cache_file = _cache_path(query, page)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data))


def _rate_limited_request(url: str, params: dict) -> dict:
    """Make a rate-limited request to the Algolia API."""
    # Wait to avoid rate limiting
    time.sleep(RATE_LIMIT_DELAY)

    headers = {
        "X-Algolia-Application-Id": "BH4D9OD16A",
        "X-Algolia-API-Key": "9bf1077c23f509069c7a5b5e5e5b5b5b",  # Public search key
    }

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def scan(query: str, limit: int = 50) -> list[RawSignal]:
    """Scan Hacker News using Algolia API.

    Queries posts matching the query, including author info and comments.
    Uses observed_at from the post/comment creation date.

    Args:
        query: Search query (e.g., "inference", "vector db", "compiler")
        limit: Maximum number of results to return

    Returns:
        List of RawSignal objects ready for bus.ingest()
    """
    # Algolia API endpoint for HN
    base_url = "https://hn.algolia.com/_template/query"

    # Query patterns to search
    search_queries = [
        f"{query} in:title,description",
        f"{query} in: url",
    ]

    raw_signals = []
    seen_ids = set()

    for search_query in search_queries:
        page = 0
        while len(raw_signals) < limit:
            # Check cache first
            cached = _load_cached(search_query, page)
            if cached is None:
                # Query Algolia
                params = {
                    "indexName": "hn_post",
                    "query": search_query,
                    "page": page,
                    "hitsPerPage": 100,
                    "tags": "",  # Filter by tags if needed
                }

                try:
                    cached = _rate_limited_request(base_url, params)
                    _save_cached(search_query, cached, page)
                except Exception as e:
                    print(f"Error fetching page {page}: {e}")
                    break

            hits = cached.get("hits", [])
            if not hits:
                break

            for hit in hits:
                if len(raw_signals) >= limit:
                    break

                object_id = hit.get("objectID")
                if object_id in seen_ids:
                    continue
                seen_ids.add(object_id)

                # Extract timestamp - use created_at_i (Unix timestamp)
                created_at = hit.get("created_at_i")
                if created_at is None:
                    # Fall back to created_at if available
                    created_at_str = hit.get("created_at")
                    if created_at_str:
                        try:
                            # Parse ISO format
                            from datetime import datetime
                            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00")).timestamp()
                        except:
                            continue  # Skip if no valid timestamp
                    else:
                        continue  # Skip if no timestamp

                observed_at = datetime.fromtimestamp(created_at, tz=timezone.utc)

                # Build payload with post data
                payload = {
                    "object_id": object_id,
                    "title": hit.get("title", ""),
                    "url": hit.get("url", ""),
                    "author": hit.get("author", ""),
                    "points": hit.get("points", 0),
                    "num_comments": hit.get("num_comments", 0),
                    "story_text": hit.get("story_text", ""),
                    "tags": hit.get("_tags", []),
                }

                # Get comments if available
                if hit.get("_nested"):
                    payload["comments"] = hit["_nested"]

                raw_signal = RawSignal(
                    source=Source.HN,
                    source_url=f"https://news.ycombinator.com/item?id={object_id}",
                    content=json.dumps(payload),
                    fetched_at=datetime.now(timezone.utc),
                    observed_at=observed_at,
                    meta={"object_id": object_id, "author": hit.get("author", "")},
                )
                raw_signals.append(raw_signal)

            page += 1

    return raw_signals[:limit]


def scan_posts_by_author(author: str, limit: int = 50) -> list[RawSignal]:
    """Scan posts by a specific author.

    Args:
        author: Hacker News username
        limit: Maximum number of posts to return

    Returns:
        List of RawSignal objects
    """
    query = f"author:{author}"
    return scan(query, limit)


def scan_comments_by_author(author: str, limit: int = 100) -> list[RawSignal]:
    """Scan comments by a specific author.

    Args:
        author: Hacker News username
        limit: Maximum number of comments to return

    Returns:
        List of RawSignal objects
    """
    # For comments, we use the hn_comment index
    base_url = "https://hn.algolia.com/_template/query"

    raw_signals = []
    seen_ids = set()

    # Search in tags for comments by author
    search_query = f"author:{author}"
    page = 0

    while len(raw_signals) < limit:
        cached = _load_cached(search_query, page)
        if cached is None:
            params = {
                "indexName": "hn_comment",
                "query": search_query,
                "page": page,
                "hitsPerPage": 100,
            }

            try:
                cached = _rate_limited_request(base_url, params)
                _save_cached(search_query, cached, page)
            except Exception as e:
                print(f"Error fetching comments page {page}: {e}")
                break

        hits = cached.get("hits", [])
        if not hits:
            break

        for hit in hits:
            if len(raw_signals) >= limit:
                break

            object_id = hit.get("objectID")
            if object_id in seen_ids:
                continue

            created_at = hit.get("created_at_i")
            if created_at is None:
                continue

            observed_at = datetime.fromtimestamp(created_at, tz=timezone.utc)

            payload = {
                "object_id": object_id,
                "author": hit.get("author", ""),
                "comment_text": hit.get("comment_text", ""),
                "parent_id": hit.get("parent_id", ""),
                "points": hit.get("points", 0),
            }

            raw_signal = RawSignal(
                source=Source.HN,
                source_url=f"https://news.ycombinator.com/item?id={object_id}",
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={"object_id": object_id, "author": hit.get("author", ""), "is_comment": True},
            )
            raw_signals.append(raw_signal)

        page += 1

    return raw_signals[:limit]


def scan_trending_topics(topics: list[str], limit_per_topic: int = 50) -> list[RawSignal]:
    """Scan multiple trending topics.

    Args:
        topics: List of topics to search
        limit_per_topic: Max results per topic

    Returns:
        Combined list of RawSignal objects
    """
    all_signals = []
    for topic in topics:
        signals = scan(topic, limit_per_topic)
        all_signals.extend(signals)
    return all_signals
