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
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from schema.events import Event, EventKind, RawSignal, Source
from sourcing.sanitize import STRIPPED_FLAG, sanitize, sanitize_tree

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

    # raw.content is not the only channel into a prompt. Everything a scanner puts in
    # meta lands in payload or evidence_span, and the memo cites evidence_span
    # verbatim — so meta takes the same funnel. See sanitize.sanitize_tree.
    ctx = {
        "source_url": raw.source_url,
        "source": raw.source,
        "observed_at": prep.observed_at,
        "entity_id": _uuid(meta.get("entity_id")),
        "company_id": _uuid(meta.get("company_id")),
    }
    meta_integrity: list[Event] = []

    payload, found = sanitize_tree({k: v for k, v in meta.items() if k not in _RESERVED}, **ctx)
    meta_integrity.extend(found)
    payload["text"] = prep.clean_text[:4000]

    span = meta.get("evidence_span")
    if span:
        span, found = sanitize_tree(span, **ctx)
        meta_integrity.extend(found)

    flags = prep.integrity_flags + list(meta.get("integrity_flags", []))
    if meta_integrity and STRIPPED_FLAG not in flags:
        flags = flags + [STRIPPED_FLAG]

    event = Event(
        kind=EventKind(meta["kind"]) if meta.get("kind") else DEFAULT_KIND[raw.source],
        source=raw.source,
        source_url=raw.source_url,
        observed_at=prep.observed_at,
        entity_id=ctx["entity_id"],
        company_id=ctx["company_id"],
        payload=payload,
        evidence_span=span or prep.clean_text[:280] or None,
        confidence=float(meta.get("confidence", 1.0)),
        integrity_flags=flags,
    )
    return prep.integrity_events + meta_integrity + [event]


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

# How long a cached raw response stays authoritative. The cache is deliberate — re-running
# the pipeline dozens of times must not cost dozens of API budgets — but "exists" is not
# "current": without a TTL the 12 files already under data/raw/github/ could never be
# refreshed, and a scan of a founder who shipped yesterday would replay last week.
# Negative disables expiry entirely; SOURCING_CACHE_TTL overrides for a demo run.
CACHE_TTL_SECONDS = float(os.environ.get("SOURCING_CACHE_TTL", 24 * 3600))

# Politeness floor between two live requests to the same host. GitHub unauthenticated is
# 60 req/hr; this does not raise that ceiling, it stops a fan-out from spending it in one
# burst and tripping the abuse detector before the rate limiter proper.
MIN_REQUEST_INTERVAL = 0.35

_last_request: dict[str, float] = {}
_stats: dict[str, int] = {"requests": 0, "cache_hits": 0, "cache_expired": 0, "errors": 0}
_quota: dict[str, dict[str, str]] = {}


def stats() -> dict:
    """What this process actually consumed. The runner prints it; nothing else reads it."""
    return {**_stats, "quota": {h: dict(v) for h, v in _quota.items()}}


def reset_stats() -> None:
    _stats.update(requests=0, cache_hits=0, cache_expired=0, errors=0)
    _quota.clear()


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
    ttl: float | None = None,
) -> str:
    """Cached GET. `refresh` forces a live fetch; `ttl` overrides CACHE_TTL_SECONDS.

    A stale cache entry is NOT served as a fallback when the refetch fails — the error
    propagates. Serving yesterday's bytes under today's timestamp is the quiet version
    of the mock-data failure this pipeline is not allowed to have.
    """
    cache_file = cache_dir / f"{_slug(url, params)}.{suffix}"
    if cache_file.exists() and not refresh:
        if _is_fresh(cache_file, CACHE_TTL_SECONDS if ttl is None else ttl):
            _stats["cache_hits"] += 1
            return cache_file.read_text()
        _stats["cache_expired"] += 1
        log.debug("cache expired for %s — refetching", url)

    body = _get(url, params, headers)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(body)
    return body


def _is_fresh(cache_file: Path, ttl: float) -> bool:
    if ttl < 0:  # explicit "never expires" — used by the backtest, which replays fixed history
        return True
    return (time.time() - cache_file.stat().st_mtime) < ttl


def _throttle(host: str) -> None:
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def _record_quota(resp: httpx.Response) -> None:
    """Whatever the host says is left. Reported by the runner rather than discovered by
    hitting the wall."""
    seen = {
        k: resp.headers[k]
        for k in ("x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset")
        if k in resp.headers
    }
    if seen:
        _quota[resp.request.url.host] = seen


def _get(url: str, params: dict | None, headers: dict | None) -> str:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    host = httpx.URL(url).host
    for attempt in range(len(BACKOFF) + 1):
        try:
            _throttle(host)
            _stats["requests"] += 1
            resp = httpx.get(
                url, params=params, headers=hdrs, timeout=TIMEOUT, follow_redirects=True
            )
        except httpx.HTTPError as exc:
            if attempt == len(BACKOFF):
                _stats["errors"] += 1
                raise FetchError(0, f"{url}: {exc}") from exc
            time.sleep(BACKOFF[attempt])
            continue

        _record_quota(resp)
        if _is_rate_limited(resp):
            _stats["errors"] += 1
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
            _stats["errors"] += 1
            raise FetchError(resp.status_code, f"{url}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.text
    _stats["errors"] += 1
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
