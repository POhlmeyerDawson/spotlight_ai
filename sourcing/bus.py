"""The one funnel. Owner: B. Inbound decks and outbound scanners take the same path.

normalize -> sanitize -> stamp observed_at -> emit Events. No special cases.

Also holds the shared fetch/cache plumbing every scanner uses, so raw responses
land under data/raw/<source>/ exactly once and rate limits are handled in one place.

`ingest()` is pure — it returns Events, it does not write them. Callers decide
what to persist via memory.store.append().
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from schema.events import Event, EventKind, RawSignal, Source
from sourcing.sanitize import sanitize

log = logging.getLogger(__name__)

DATE_INFERRED = "date_inferred"

# What a source emits when meta doesn't say otherwise.
DEFAULT_KIND: dict[Source, EventKind] = {
    Source.HN: EventKind.HN_POST,
    Source.GITHUB: EventKind.REPO_ACTIVITY,
    Source.ARXIV: EventKind.PAPER,
    Source.WEB: EventKind.PROFILE_FACT,
    Source.DECK: EventKind.DECK_CLAIM,
    Source.PROOF_PROTOCOL: EventKind.PROOF_ARTIFACT,
    Source.VALIDATOR: EventKind.VALIDATION_RESULT,
    Source.MANUAL: EventKind.PROFILE_FACT,
}

# meta keys the bus consumes itself; everything else is passed through to payload.
_RESERVED = {
    "kind",
    "observed_at",
    "date_floor",
    "evidence_span",
    "confidence",
    "entity_id",
    "company_id",
    "integrity_flags",
}


@dataclass
class Prepared:
    """Result of the shared normalize->sanitize->stamp path, before events are shaped.

    deck.py uses this directly: it needs sanitized per-slide text to feed the claim
    extractor, but must not skip the funnel to get it.
    """

    clean_text: str
    observed_at: datetime
    integrity_flags: list[str] = field(default_factory=list)
    integrity_events: list[Event] = field(default_factory=list)


def prepare(raw: RawSignal) -> Prepared:
    text = raw.content.decode("utf-8", "replace") if isinstance(raw.content, bytes) else raw.content
    observed_at, flags = _stamp(raw)
    clean, integrity = sanitize(
        text,
        source_url=raw.source_url,
        source=raw.source,
        observed_at=observed_at,
        entity_id=_uuid(raw.meta.get("entity_id")),
        company_id=_uuid(raw.meta.get("company_id")),
    )
    if integrity:
        flags = flags + [f.integrity_flags[0] for f in integrity[:1]]
    return Prepared(clean, observed_at, flags, integrity)


def ingest(raw: RawSignal) -> list[Event]:
    prep = prepare(raw)
    meta = raw.meta
    payload = {k: v for k, v in meta.items() if k not in _RESERVED}
    payload["text"] = prep.clean_text[:4000]

    event = Event(
        kind=EventKind(meta["kind"]) if meta.get("kind") else DEFAULT_KIND[raw.source],
        source=raw.source,
        source_url=raw.source_url,
        observed_at=prep.observed_at,
        entity_id=_uuid(meta.get("entity_id")),
        company_id=_uuid(meta.get("company_id")),
        payload=payload,
        evidence_span=meta.get("evidence_span") or prep.clean_text[:280] or None,
        confidence=float(meta.get("confidence", 1.0)),
        integrity_flags=prep.integrity_flags + list(meta.get("integrity_flags", [])),
    )
    return prep.integrity_events + [event]


def _stamp(raw: RawSignal) -> tuple[datetime, list[str]]:
    """observed_at comes from the source's own clock, or it is flagged. Never silently now().

    Ladder: the source's real timestamp -> the earliest date we can actually defend
    (scanner-supplied floor, e.g. a date in the URL) -> fetch time, which is the only
    remaining defensible bound. The last two carry date_inferred, so the backtest can
    see exactly which signals it should not trust the clock on.
    """
    real = parse_ts(raw.meta.get("observed_at"))
    if real:
        return real, []
    floor = parse_ts(raw.meta.get("date_floor"))
    if floor:
        return floor, [DATE_INFERRED]
    # Conservative on purpose: fetch time never grants retroactive credit in the backtest.
    return raw.fetched_at, [DATE_INFERRED]


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %b %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


def parse_ts(value: Any) -> datetime | None:
    """Every scanner's timestamps land here. Returns tz-aware UTC or None — never a guess."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):  # unix epoch
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Shared HTTP + raw cache. Scanners call these; nothing here knows about Events.
# ---------------------------------------------------------------------------

BACKOFF = (1.0, 3.0)  # bounded on purpose — a scanner must never hang the pipeline
TIMEOUT = 15.0
USER_AGENT = "vc-brain-sourcing/0.1"


class FetchError(RuntimeError):
    def __init__(self, status: int, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RateLimited(FetchError):
    """Raised immediately rather than slept through — resets are minutes-to-hours away."""


def fetch_json(url: str, params: dict | None = None, *, cache_dir: Path, **kw) -> Any:
    return json.loads(fetch_text(url, params, cache_dir=cache_dir, suffix="json", **kw))


def fetch_text(
    url: str,
    params: dict | None = None,
    *,
    cache_dir: Path,
    headers: dict | None = None,
    suffix: str = "txt",
    refresh: bool = False,
) -> str:
    cache_file = cache_dir / f"{_slug(url, params)}.{suffix}"
    if cache_file.exists() and not refresh:
        return cache_file.read_text()

    body = _get(url, params, headers)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(body)
    return body


def _get(url: str, params: dict | None, headers: dict | None) -> str:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(len(BACKOFF) + 1):
        try:
            resp = httpx.get(
                url, params=params, headers=hdrs, timeout=TIMEOUT, follow_redirects=True
            )
        except httpx.HTTPError as exc:
            if attempt == len(BACKOFF):
                raise FetchError(0, f"{url}: {exc}") from exc
            time.sleep(BACKOFF[attempt])
            continue

        if _is_rate_limited(resp):
            retry = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset")
            raise RateLimited(
                resp.status_code,
                f"rate limited by {httpx.URL(url).host} (reset={retry}); "
                f"set GITHUB_TOKEN or wait before re-running",
                retry_after=float(retry) if retry and retry.isdigit() else None,
            )
        if resp.status_code >= 500 and attempt < len(BACKOFF):
            time.sleep(BACKOFF[attempt])
            continue
        if resp.status_code >= 400:
            raise FetchError(resp.status_code, f"{url}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.text
    raise FetchError(0, f"{url}: exhausted retries")


def _is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    return resp.status_code == 403 and (
        resp.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in resp.text.lower()
    )


def _slug(url: str, params: dict | None) -> str:
    key = hashlib.sha1(f"{url}{sorted((params or {}).items())}".encode()).hexdigest()[:12]
    tail = re.sub(r"[^a-zA-Z0-9]+", "_", httpx.URL(url).path.strip("/"))[:48] or "root"
    return f"{tail}_{key}"
