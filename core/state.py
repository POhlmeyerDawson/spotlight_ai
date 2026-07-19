"""Shared server state that must survive a process boundary.

WHY THIS MODULE EXISTS. Migration 002 already made the argument for the login throttle:
"in the database rather than in process memory because a serverless deployment runs many
short-lived processes, and an in-memory counter there is a rate limit that resets on
every cold start." Three more pieces of state had the same shape and had not been moved
— the dissent lock, the proof-challenge issue record, and the edited thesis — and on
Vercel each request may land on a different lambda, so all three broke. Migration 008
adds the tables; this module is the only thing that talks to them.

It lives in `core/` rather than in `api/routers/deps.py` because `core.thesis` needs it
and `core` must never import `api`. What is deliberately NOT here is anything HTTP:
deciding WHO is asking is `api.routers.deps.viewer_scope`, because that reads cookies.

THE RULE FOR EVERY FUNCTION HERE: it does not raise. Each caller has a fallback, because
a demo where a database hiccup takes the product down is worse than one where a single
behaviour is narrower than it should be. That is why reads return None for "could not
answer" and never conflate it with an empty result — "no row" and "cannot tell" lead to
different decisions at every call site.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: Both dialects accept these verbatim; SQLite ignores the column type names. Run on
#: first use so the tables exist even against a database where migrations have not been
#: applied, and on SQLite, which has no migration runner at all. Migration 008 carries
#: the same DDL plus the Postgres-only RLS posture.
_STATE_DDL = (
    "create table if not exists dissent_unlocks ("
    " scope text not null, company_id text not null,"
    " served_at timestamptz not null, primary key (scope, company_id))",
    "create table if not exists proof_challenges ("
    " challenge_id text primary key, company_id text, issued_at timestamptz not null)",
    "create table if not exists config_documents ("
    " key text primary key, document text not null, updated_at timestamptz not null)",
)

TABLES = ("dissent_unlocks", "proof_challenges", "config_documents")

# Keyed by id() but HOLDING the connection, for the reason memory/profiles.py documents:
# CPython reuses the id of a freed object, so a bare set of ints would report "already
# ensured" for a brand-new connection and the tables would silently not exist.
_ensured: dict[int, Any] = {}


def conn() -> Any:
    from memory import db

    c = db.connect()
    if _ensured.get(id(c)) is not c:
        for ddl in _STATE_DDL:
            c.execute(ddl)
        c.commit()
        _ensured[id(c)] = c
    return c


def write(sql: str, args: tuple | list = ()) -> bool:
    """True if it landed. False means the caller must fall back — and, where the write
    was a user's edit rather than a cache fill, must SAY it did not save."""
    try:
        c = conn()
        c.execute(sql, args)
        c.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - shared state is best-effort by design
        log.info("shared state write unavailable (%s): %s", type(exc).__name__, exc)
        return False


def fetch(sql: str, args: tuple | list = ()) -> list[dict] | None:
    """Rows, or None when the store could not answer. None is NOT an empty result."""
    try:
        return [dict(row) for row in conn().execute(sql, args).fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.info("shared state read unavailable (%s): %s", type(exc).__name__, exc)
        return None


def reset() -> None:
    """Test hook. Drops the rows, not the tables."""
    for table in TABLES:
        write(f"delete from {table}")
    _ensured.clear()
    _documents.clear()


# ---------------------------------------------------------------------------
# Config documents. Mutable JSON config that used to be a file on a read-only disk.
# ---------------------------------------------------------------------------


#: Seconds a fetched config document is reused before the store is asked again.
#:
#: THIS TTL IS LOAD-BEARING, not a micro-optimisation. `core.thesis.load()` is called
#: 336 times while serving a single GET /companies — every scope check and every gate
#: evaluation reads the thesis — and it used to be a local file read, which is free.
#: Routing it to a hosted Postgres without this cache added EIGHT SECONDS to the ranked
#: list, measured. A config document is read constantly and written by hand a few times
#: a session, so the correct shape is a short read-through cache.
#:
#: The staleness this admits is bounded and local: a PUT invalidates the cache in its own
#: process immediately, so the editor always sees their own edit. Another process can lag
#: by up to this long, which for a thesis — config, not a fact about a founder — is fine.
_DOCUMENT_TTL_SECONDS = 30.0

#: key -> (monotonic fetch time, document or None). A cached None is a REAL cached
#: answer: "there is no stored document" is the common case and must not re-query on
#: every one of those 336 calls either.
_documents: dict[str, tuple[float, dict | None]] = {}


def get_document(key: str, *, refresh: bool = False) -> dict | None:
    """The stored document, or None for "no document" AND for "cannot tell".

    Those two are collapsed HERE on purpose and only here: every reader of a config
    document has the same fallback — the file shipped in data/seed — so the distinction
    changes nothing for them. The WRITER must not collapse them, and does not.
    """
    import json
    import time

    cached = _documents.get(key)
    if not refresh and cached is not None and time.monotonic() - cached[0] < _DOCUMENT_TTL_SECONDS:
        return cached[1]

    rows = fetch("select document from config_documents where key = ?", (key,))
    doc: dict | None = None
    if rows:
        try:
            parsed = json.loads(rows[0]["document"])
            doc = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            doc = None
    # A failed read (rows is None) is cached as "no document" for the TTL as well. The
    # alternative is hammering an unreachable database 336 times per request, which turns
    # a degraded page into a timed-out one.
    _documents[key] = (time.monotonic(), doc)
    return doc


def put_document(key: str, document: dict) -> bool:
    """Store a config document. False means IT DID NOT SAVE and the caller must say so."""
    import json

    from schema.events import utcnow

    ok = write(
        "insert into config_documents (key, document, updated_at) values (?, ?, ?) "
        "on conflict (key) do update set document = excluded.document, "
        "updated_at = excluded.updated_at",
        (key, json.dumps(document), utcnow().isoformat()),
    )
    # Invalidate either way. On success the cache is stale; on failure it may hold a
    # value this process just tried to replace, and serving that back would misreport a
    # rejected edit as the current config.
    _documents.pop(key, None)
    return ok
