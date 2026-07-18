"""SQLite backing store. Owner: A.

Supabase has no credentials yet, so the event log lives in a local SQLite file. The DDL
below is 001_init.sql translated: uuid -> TEXT, timestamptz -> TEXT (ISO8601 UTC),
jsonb/text[] -> TEXT holding JSON, no extensions. The two as_of read-path indexes and
the append-only trigger survive the translation — those are the parts that matter.

Swap point for Postgres: connect() and the DDL. Nothing above this file knows the driver.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = "data/vcbrain.db"

SCHEMA = """
create table if not exists entities (
    entity_id       text primary key,
    display_name    text not null,
    name_normalized text not null,
    created_at      text not null
);

create table if not exists entity_aliases (
    alias_id  text primary key,
    entity_id text not null references entities(entity_id),
    kind      text not null,          -- 'email' | 'url' | 'handle' | 'name'
    value     text not null,
    source    text not null,
    unique (kind, value)
);

create table if not exists companies (
    company_id         text primary key,
    name               text not null,
    founder_entity_ids text not null default '[]',   -- json array of uuid strings
    archetype          integer,                      -- 1..6, seed data only
    created_at         text not null
);

create table if not exists events (
    event_id        text primary key,
    entity_id       text references entities(entity_id),
    company_id      text references companies(company_id),
    kind            text not null,
    source          text not null,
    source_url      text,
    observed_at     text not null,                   -- when the world produced it
    ingested_at     text not null,
    payload         text not null default '{}',      -- json object
    evidence_span   text,
    confidence      real not null default 1.0 check (confidence between 0 and 1),
    integrity_flags text not null default '[]'       -- json array
);

-- Every read path is as_of-scoped. These two indexes are the read path.
create index if not exists idx_events_entity_observed on events (entity_id, observed_at);
create index if not exists idx_events_company_observed on events (company_id, observed_at);
create index if not exists idx_events_kind on events (kind);

create table if not exists merges (
    merge_id   text primary key,
    entity_a   text not null references entities(entity_id),
    entity_b   text not null references entities(entity_id),
    status     text not null check (status in ('merged', 'ambiguous', 'rejected')),
    score      real not null,
    rationale  text not null,
    decided_at text not null
);

-- Append-only enforcement, at the DB level rather than by convention.
create trigger if not exists events_no_update before update on events
begin
    select raise(abort, 'events is append-only: corrections are new events, not updates');
end;

create trigger if not exists events_no_delete before delete on events
begin
    select raise(abort, 'events is append-only: corrections are new events, not deletes');
end;
"""

_conns: dict[str, sqlite3.Connection] = {}


def db_path() -> str:
    """VCBRAIN_DB_PATH wins so tests can point at a tmp_path file."""
    override = os.getenv("VCBRAIN_DB_PATH")
    if override:
        return override
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    return DEFAULT_PATH


def connect(path: str | None = None) -> sqlite3.Connection:
    """Cached per-path connection; creates the schema on first use."""
    path = path or db_path()
    conn = _conns.get(path)
    if conn is not None:
        return conn
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # foreign_keys stays off (SQLite default): B stamps entity_id/company_id from resolution
    # before those rows necessarily exist. An unresolvable id must not reject an observation.
    conn.executescript(SCHEMA)
    conn.commit()
    _conns[path] = conn
    return conn


def reset_connections() -> None:
    """Drop cached handles — used by tests that repoint VCBRAIN_DB_PATH."""
    for conn in _conns.values():
        conn.close()
    _conns.clear()


def to_iso(dt: datetime) -> str:
    """UTC with fixed-width microseconds, so lexical order == chronological order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def from_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
