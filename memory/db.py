"""Backing store. Owner: A.

Two backends, chosen by the DATABASE_URL scheme: `sqlite://` (default, and what the
whole test suite runs on) and `postgresql://` (Supabase). VCBRAIN_DB_PATH always forces
SQLite — tests must never reach the network.

Call sites are written in the SQLite dialect (`?` params, `insert or ignore`). The
Postgres connection translates that centrally in _translate(), and its row factory hands
back exactly what SQLite would: uuid -> str, timestamptz -> ISO string, jsonb -> JSON
string. So nothing above this file knows which backend it is talking to.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

# The backend is chosen from DATABASE_URL, so .env has to be loaded even when nothing
# imported core.config first (scripts/seed.py, for one). Never overrides a real env var.
load_dotenv()

DEFAULT_PATH = "data/vcbrain.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "schema" / "migrations"

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

SQLITE = "sqlite"
POSTGRES = "postgres"

_conns: dict[str, Any] = {}


def backend() -> str:
    """VCBRAIN_DB_PATH always means SQLite; otherwise the DATABASE_URL scheme decides."""
    if os.getenv("VCBRAIN_DB_PATH"):
        return SQLITE
    url = os.getenv("DATABASE_URL", "")
    return POSTGRES if url.startswith(("postgresql://", "postgres://")) else SQLITE


def db_path() -> str:
    override = os.getenv("VCBRAIN_DB_PATH")
    if override:
        return override
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    return DEFAULT_PATH


def connect(path: str | None = None) -> Any:
    """Cached connection; creates/migrates the schema on first use.

    An explicit path forces SQLite — callers passing a filename mean a file.
    """
    if path is None and backend() == POSTGRES:
        return _connect_postgres(os.environ["DATABASE_URL"])
    return _connect_sqlite(path or db_path())


def _connect_sqlite(path: str) -> sqlite3.Connection:
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


def _connect_postgres(dsn: str) -> PgConnection:
    conn = _conns.get(dsn)
    if conn is None:
        conn = PgConnection(dsn)
        apply_migrations(conn)
        _conns[dsn] = conn
    return conn


def reset_connections() -> None:
    """Drop cached handles — used by tests that repoint VCBRAIN_DB_PATH."""
    for conn in _conns.values():
        conn.close()
    _conns.clear()


# ---------------------------------------------------------------------------
# Postgres: dialect translation + a connection that survives an idle pooler drop
# ---------------------------------------------------------------------------


def _translate(sql: str) -> str:
    """SQLite dialect -> Postgres. `?` becomes `%s`, literal `%` is doubled for psycopg,
    and `insert or ignore` becomes an `on conflict do nothing` suffix."""
    lowered = sql.lstrip().lower()
    ignore = lowered.startswith("insert or ignore")
    if ignore:
        head = sql.lstrip()
        sql = "insert" + head[len("insert or ignore") :]

    out: list[str] = []
    in_literal = False
    for ch in sql:
        if ch == "'":
            in_literal = not in_literal
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not in_literal:
            out.append("%s")
        else:
            out.append(ch)
    translated = "".join(out)
    return f"{translated} on conflict do nothing" if ignore else translated


def _coerce(value: Any) -> Any:
    """Make a Postgres value indistinguishable from what SQLite would have returned."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return to_iso(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


class _Rows(list):
    """A materialized result set that quacks like a DB-API cursor."""

    def fetchall(self) -> list[dict]:
        return list(self)

    def fetchone(self) -> dict | None:
        return self[0] if self else None


class PgConnection:
    """Module-level Postgres handle. Autocommit, so a rejected write (the append-only
    trigger) never leaves the session in an aborted transaction. Reconnects once on a
    dropped connection — the Supabase session pooler hangs up on idle sessions and a
    demo that dies after five idle minutes is worse than SQLite."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._lock = threading.RLock()
        self._conn: Any = None

    def _raw(self) -> Any:
        import psycopg

        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def execute(self, sql: str, args: Any = ()) -> _Rows:
        import psycopg

        translated = _translate(sql)
        with self._lock:
            for attempt in (0, 1):
                try:
                    return self._run(translated, tuple(args))
                except (psycopg.OperationalError, psycopg.InterfaceError):
                    if attempt:
                        raise
                    self._discard()

    def _run(self, sql: str, args: tuple) -> _Rows:
        from psycopg.rows import dict_row

        with self._raw().cursor(row_factory=dict_row) as cur:
            cur.execute(sql, args or None)
            if cur.description is None:
                return _Rows()
            return _Rows({k: _coerce(v) for k, v in row.items()} for row in cur.fetchall())

    def executescript(self, sql: str) -> None:
        """Raw multi-statement SQL, no dialect translation (migrations are Postgres-native)."""
        with self._lock:
            self._raw().execute(sql)

    def _discard(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001 - already-dead connection
            pass
        self._conn = None

    def commit(self) -> None:
        """No-op: the connection is autocommit."""

    def close(self) -> None:
        with self._lock:
            self._discard()


def apply_migrations(conn: PgConnection) -> list[str]:
    """Applies every schema/migrations/*.sql not yet recorded. Returns what it applied."""
    conn.executescript(
        "create table if not exists schema_migrations ("
        "  filename text primary key,"
        "  applied_at timestamptz not null default now())"
    )
    done = {r["filename"] for r in conn.execute("select filename from schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        conn.executescript(path.read_text())
        conn.execute("insert into schema_migrations (filename) values (?)", (path.name,))
        applied.append(path.name)
    return applied


def to_iso(dt: datetime) -> str:
    """UTC with fixed-width microseconds, so lexical order == chronological order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def from_iso(s: str | datetime) -> datetime:
    dt = s if isinstance(s, datetime) else datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
