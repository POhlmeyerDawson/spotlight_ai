"""Shared helpers for the routers. Owner: D.

Two jobs: load seed fixtures, and degrade gracefully when a teammate's module still
raises NotImplementedError. The app must ALWAYS run — a dead route at hour 23 is a
dead demo beat, so every live call has a fixture behind it.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import UUID

from fastapi import HTTPException

log = logging.getLogger(__name__)

T = TypeVar("T")
U = TypeVar("U")


def seed_dir() -> Path:
    """VCBRAIN_SEED_DIR wins so tests can point at a tmp fixture set."""
    return Path(os.getenv("VCBRAIN_SEED_DIR", "data/seed"))


def seed(name: str) -> dict:
    path = seed_dir() / f"{name}.json"
    if not path.exists():
        raise HTTPException(404, f"no seed fixture: {name}")
    return json.loads(path.read_text())


def seed_or(name: str, default: Any) -> Any:
    path = seed_dir() / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else default


def fixture_key(company_id: str) -> str:
    """Map a store UUID back to its fixture slug.

    /companies returns real UUIDs from the store, but the detail/memo/dissent
    fixtures are named by slug (company_vb-tensorpage.json). Without this the
    primary navigation — click a row in the ranked list — 404s on every company.
    Passes non-UUID input straight through, so slugs still work directly.

    Resolved through the company NAME rather than the generated _resolved_ids.json:
    UUIDs are minted fresh whenever the database is rebuilt, so any file mapping
    slug->uuid is stale the moment someone reseeds. Names are stable.
    """
    cid = as_uuid(company_id)
    if cid is None:
        return company_id

    from memory import store

    row = store.get_company(cid) or {}
    name = row.get("name")
    if not name:
        return company_id
    return _slug_by_name().get(name, company_id)


@lru_cache(maxsize=1)
def prior_company_names() -> frozenset[str]:
    """Companies a serial founder ran BEFORE the current one — history, not pipeline."""
    out: set[str] = set()
    for path in sorted(seed_dir().glob("archetype_*.json")):
        for profile in json.loads(path.read_text()).get("profiles", []):
            out |= {p["name"] for p in profile.get("prior_companies", [])}
    return frozenset(out)


@lru_cache(maxsize=1)
def _slug_by_name() -> dict[str, str]:
    """company_name -> fixture slug, read from the archetype fixtures themselves."""
    out: dict[str, str] = {}
    for path in sorted(seed_dir().glob("archetype_*.json")):
        for profile in json.loads(path.read_text()).get("profiles", []):
            out[profile["company_name"]] = profile["company_id"]
            for prior in profile.get("prior_companies", []):
                out[prior["name"]] = prior["company_id"]
    return out


def degrade(live: Callable[[], T], fallback: Callable[[], U]) -> T | U:
    """Run the real module; fall back to fixtures on anything short of a 4xx.

    NotImplementedError is the expected case while branches A/B/C are still landing.
    """
    try:
        return live()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - degrading is the whole point
        log.info("degraded to fixture (%s): %s", type(exc).__name__, exc)
        return fallback()


def as_uuid(value: str | UUID | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_as_of(as_of: datetime | None) -> datetime:
    """Naive datetimes silently break as_of comparisons — normalize at the boundary."""
    if as_of is None:
        return now()
    return as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)


def pick(d: dict, *keys: str, default: Any = None) -> Any:
    """Tolerant accessor — fixture key names are still settling across branches."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def founder_entity_ids(company_id: UUID) -> list[UUID]:
    """Founder entities for a company, via the store; empty when unresolvable."""
    from memory import store

    row = store.get_company(company_id) or {}
    raw = row.get("founder_entity_ids") or "[]"
    ids = json.loads(raw) if isinstance(raw, str) else raw
    resolved = [u for u in (as_uuid(i) for i in ids) if u is not None]
    if resolved:
        return resolved

    # Fall back to the event log. The column is a denormalized convenience that
    # ingestion does not always populate, but events always carry entity_id —
    # so the log is the authority. Without this every founder scores at the
    # prior, because the scorer is never handed an entity to score.
    from schema.events import utcnow

    seen: dict[UUID, None] = {}
    for e in store.events(as_of=utcnow(), company_id=company_id):
        if e.entity_id is not None:
            seen.setdefault(e.entity_id, None)
    return list(seen)


@lru_cache(maxsize=1)
def _seed_files_cached(dir_key: str, stamp: float) -> tuple[dict, ...]:
    return tuple(json.loads(p.read_text()) for p in sorted(Path(dir_key).glob("*.json")))


def all_seed_blobs() -> list[dict]:
    d = seed_dir()
    if not d.exists():
        return []
    stamp = max((p.stat().st_mtime for p in d.glob("*.json")), default=0.0)
    return list(_seed_files_cached(str(d), stamp))


def find_seed_event(event_id: str) -> dict | None:
    """Scan every fixture for an event with this id. Fixture shapes are still moving,
    so walk the whole structure rather than assuming a key."""

    def walk(node: Any) -> dict | None:
        if isinstance(node, dict):
            if str(node.get("event_id", "")) == event_id:
                return node
            for v in node.values():
                if (hit := walk(v)) is not None:
                    return hit
        elif isinstance(node, list):
            for v in node:
                if (hit := walk(v)) is not None:
                    return hit
        return None

    for blob in all_seed_blobs():
        if (hit := walk(blob)) is not None:
            return hit
    return None
