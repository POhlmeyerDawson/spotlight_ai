"""Apply schema/migrations/*.sql to whatever DATABASE_URL points at. Owner: A.

    uv run python scripts/migrate.py                    # migrate
    uv run python scripts/migrate.py --from-sqlite      # + copy data/vcbrain.db into it

Idempotent on both counts: migrations are recorded in schema_migrations, and the copy
inserts with `on conflict do nothing`, so a second run moves nothing.

Re-seeding (`scripts/seed.py` then `core.pipeline.derive_all`) also works and is the
cleaner path for a fresh database, since seed event ids are uuid5 and therefore stable.
--from-sqlite exists for the case where a local store already holds derived events you
do not want to recompute.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory import db  # noqa: E402

TABLES = ("entities", "companies", "entity_aliases", "events", "merges")


def migrate() -> None:
    if db.backend() != db.POSTGRES:
        print(f"backend: sqlite ({db.db_path()}) — schema is created on connect, nothing to do")
        db.connect()
        return
    conn = db.connect()  # applies migrations on first connect
    applied = db.apply_migrations(conn)
    print(f"backend: postgres ({_host()})")
    if applied:
        print(f"  applied: {', '.join(applied)}")
    else:
        print("  already up to date")
    for table in TABLES:
        n = conn.execute(f"select count(*) as n from {table}").fetchone()["n"]
        print(f"  {table:<15} {n:>6} rows")


def copy_from_sqlite(path: str) -> None:
    if db.backend() != db.POSTGRES:
        raise SystemExit("--from-sqlite needs DATABASE_URL to point at postgres")
    src = db._connect_sqlite(path)
    dst = db.connect()
    print(f"copying {path} -> postgres ({_host()})")
    for table in TABLES:
        rows = [dict(r) for r in src.execute(f"select * from {table}")]
        if not rows:
            print(f"  {table:<15} empty")
            continue
        cols = list(rows[0])
        sql = (
            f"insert or ignore into {table} ({', '.join(cols)}) "
            f"values ({', '.join('?' for _ in cols)})"
        )
        before = dst.execute(f"select count(*) as n from {table}").fetchone()["n"]
        for row in rows:
            dst.execute(sql, [row[c] for c in cols])
        after = dst.execute(f"select count(*) as n from {table}").fetchone()["n"]
        print(f"  {table:<15} {len(rows):>6} read, {after - before:>6} inserted, {after} total")


def _host() -> str:
    import os
    from urllib.parse import urlsplit

    return urlsplit(os.environ["DATABASE_URL"]).hostname or "?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from-sqlite",
        nargs="?",
        const=db.DEFAULT_PATH,
        metavar="PATH",
        help="copy an existing SQLite store into the target database after migrating",
    )
    args = ap.parse_args()
    migrate()
    if args.from_sqlite:
        copy_from_sqlite(args.from_sqlite)


if __name__ == "__main__":
    main()
