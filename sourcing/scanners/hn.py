"""hn scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.
"""

from __future__ import annotations

from pathlib import Path

from schema.events import EventKind, RawSignal, Source
from sourcing import bus

API = "https://hn.algolia.com/api/v1/search"
ITEM_API = "https://hn.algolia.com/api/v1/items"
CACHE = Path("data/raw/hn")

MAX_PER_PAGE = 100


def scan(query: str, limit: int = 50) -> list[RawSignal]:
    data = bus.fetch_json(
        API,
        {"query": query, "tags": "(story,comment)", "hitsPerPage": min(limit, MAX_PER_PAGE)},
        cache_dir=CACHE,
    )
    return parse(data)[:limit]


def parse(data: dict) -> list[RawSignal]:
    signals = []
    for hit in data.get("hits", []):
        content = "\n".join(
            p for p in (hit.get("title"), hit.get("story_text"), hit.get("comment_text")) if p
        )
        if not content.strip():
            continue
        is_comment = bool(hit.get("comment_text"))
        object_id = hit.get("objectID")
        signals.append(
            RawSignal(
                source=Source.HN,
                source_url=hit.get("url") or f"{ITEM_API}/{object_id}",
                content=content,
                meta={
                    "kind": str(EventKind.HN_COMMENT if is_comment else EventKind.HN_POST),
                    "observed_at": hit.get("created_at"),  # the source's own clock
                    "author": hit.get("author"),
                    "points": hit.get("points"),
                    "num_comments": hit.get("num_comments"),
                    "object_id": object_id,
                    "story_id": hit.get("story_id"),
                    "evidence_span": (hit.get("title") or content)[:200],
                },
            )
        )
    return signals
