"""arxiv scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.

observed_at is <published> — the v1 submission date, not <updated>: a v3 revision in
2025 does not mean the work landed in 2025.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from schema.events import EventKind, RawSignal, Source
from sourcing import bus

API = "https://export.arxiv.org/api/query"
CACHE = Path("data/raw/arxiv")
CATEGORIES = ("cs.LG", "cs.DC", "cs.PL")

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def scan(query: str, limit: int = 50) -> list[RawSignal]:
    cats = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    xml = bus.fetch_text(
        API,
        {
            "search_query": f"({cats}) AND all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        cache_dir=CACHE,
        suffix="xml",
    )
    return parse(xml)[:limit]


def parse(xml: str) -> list[RawSignal]:
    root = ElementTree.fromstring(xml)
    signals = []
    for entry in root.findall("atom:entry", NS):
        title = _text(entry, "atom:title")
        summary = _text(entry, "atom:summary")
        arxiv_id = _text(entry, "atom:id")
        published = _text(entry, "atom:published")
        if not published:
            continue  # no honest timestamp, no ingestion
        signals.append(
            RawSignal(
                source=Source.ARXIV,
                source_url=arxiv_id or None,
                content=f"{title}\n{summary}".strip(),
                meta={
                    "kind": str(EventKind.PAPER),
                    "observed_at": published,  # v1 submission
                    "arxiv_id": arxiv_id.rsplit("/", 1)[-1] if arxiv_id else None,
                    "authors": _authors(entry),
                    "categories": [
                        c.get("term") for c in entry.findall("atom:category", NS) if c.get("term")
                    ],
                    "primary_category": _attr(entry, "arxiv:primary_category", "term"),
                    "evidence_span": title[:200],
                },
            )
        )
    return signals


def _authors(entry: ElementTree.Element) -> list[dict]:
    """Affiliation is recorded as a FACT and is never a scoring input (Invariant #3)."""
    out = []
    for a in entry.findall("atom:author", NS):
        out.append(
            {
                "name": _text(a, "atom:name"),
                "affiliation_fact_only": _text(a, "arxiv:affiliation") or None,
            }
        )
    return out


def _text(node: ElementTree.Element, path: str) -> str:
    found = node.find(path, NS)
    return " ".join((found.text or "").split()) if found is not None else ""


def _attr(node: ElementTree.Element, path: str, attr: str) -> str | None:
    found = node.find(path, NS)
    return found.get(attr) if found is not None else None
