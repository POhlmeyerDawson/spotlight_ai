"""Backend selection and dialect translation. Never connects to Postgres."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory import db

PG_URL = "postgresql://u:p@example.invalid:5432/postgres"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.delenv("VCBRAIN_DB_PATH", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.reset_connections()
    yield
    db.reset_connections()


# --- URL-scheme dispatch ---------------------------------------------------


def test_backend_defaults_to_sqlite() -> None:
    assert db.backend() == db.SQLITE
    assert db.db_path() == db.DEFAULT_PATH


def test_postgres_url_selects_postgres(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    assert db.backend() == db.POSTGRES


def test_sqlite_url_selects_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    assert db.backend() == db.SQLITE
    assert db.db_path().endswith("x.db")


def test_db_path_override_beats_postgres_url(monkeypatch, tmp_path) -> None:
    """The whole test suite depends on this: VCBRAIN_DB_PATH forces SQLite, no network."""
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "t.db"))
    assert db.backend() == db.SQLITE
    import sqlite3

    assert isinstance(db.connect(), sqlite3.Connection)


def test_explicit_path_forces_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    import sqlite3

    assert isinstance(db.connect(str(tmp_path / "explicit.db")), sqlite3.Connection)


def test_postgres_branch_builds_a_pg_connection(monkeypatch) -> None:
    """Inspect the dispatch without connecting: PgConnection is lazy until execute()."""
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setattr(db, "apply_migrations", lambda conn: [])
    conn = db.connect()
    assert isinstance(conn, db.PgConnection)
    assert conn._conn is None  # nothing was dialled
    assert db.connect() is conn  # cached


# --- dialect translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "select * from events where observed_at <= ?",
            "select * from events where observed_at <= %s",
        ),
        ("insert into t (a, b) values (?,?)", "insert into t (a, b) values (%s,%s)"),
        ("select * from t", "select * from t"),
        # literal % must be doubled for psycopg's placeholder parser
        ("select * from t where name like '%foo%'", "select * from t where name like '%%foo%%'"),
        # a ? inside a string literal is data, not a placeholder
        ("select * from t where q = '?'", "select * from t where q = '?'"),
    ],
)
def test_translate_param_style(sql: str, expected: str) -> None:
    assert db._translate(sql) == expected


def test_translate_insert_or_ignore() -> None:
    out = db._translate("insert or ignore into entity_aliases (kind, value) values (?,?)")
    assert out == "insert into entity_aliases (kind, value) values (%s,%s) on conflict do nothing"


# --- row coercion: postgres rows must look exactly like sqlite rows --------


def test_coerce_matches_sqlite_representation() -> None:
    from uuid import UUID

    uid = UUID("6f1a3c2e-9b47-4d51-a8e0-2c7f5b91d403")
    dt = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    assert db._coerce(uid) == str(uid)
    assert db._coerce(dt) == db.to_iso(dt)
    assert db._coerce({"a": 1}) == '{"a": 1}'
    assert db._coerce(["x"]) == '["x"]'
    assert db._coerce(None) is None
    assert db._coerce(0.5) == 0.5


def test_from_iso_accepts_datetime_and_string() -> None:
    """Postgres hands back timestamptz as datetime; SQLite hands back an ISO string."""
    dt = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    assert db.from_iso(dt) == dt
    assert db.from_iso(db.to_iso(dt)) == dt
    naive = datetime(2024, 1, 1, 12)
    assert db.from_iso(naive).tzinfo == timezone.utc
