"""Append-only event store. Owner: A.

Note the signature: as_of is REQUIRED and has no default. That is deliberate —
it makes the lookahead bug hard to write rather than merely discouraged.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from memory import db
from schema.events import Event, utcnow


def append(event: Event) -> UUID:
    conn = db.connect()
    conn.execute(
        "insert into events (event_id, entity_id, company_id, kind, source, source_url, "
        "observed_at, ingested_at, payload, evidence_span, confidence, integrity_flags) "
        "values (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(event.event_id),
            str(event.entity_id) if event.entity_id else None,
            str(event.company_id) if event.company_id else None,
            str(event.kind),
            str(event.source),
            event.source_url,
            db.to_iso(event.observed_at),
            db.to_iso(event.ingested_at),
            json.dumps(event.payload),
            event.evidence_span,
            event.confidence,
            json.dumps(event.integrity_flags),
        ),
    )
    conn.commit()
    return event.event_id


def events(
    *,
    as_of: datetime,
    entity_id: UUID | None = None,
    company_id: UUID | None = None,
    kind: str | None = None,
) -> list[Event]:
    """Returns only events with observed_at <= as_of. No exceptions, no flags."""
    sql = ["select * from events where observed_at <= ?"]
    args: list[Any] = [db.to_iso(as_of)]
    if entity_id is not None:
        sql.append("and entity_id = ?")
        args.append(str(entity_id))
    if company_id is not None:
        sql.append("and company_id = ?")
        args.append(str(company_id))
    if kind is not None:
        sql.append("and kind = ?")
        args.append(str(kind))
    sql.append("order by observed_at")
    rows = db.connect().execute(" ".join(sql), args).fetchall()
    return [_row_to_event(r) for r in rows]


def _row_to_event(row: Any) -> Event:
    return Event(
        event_id=UUID(row["event_id"]),
        entity_id=UUID(row["entity_id"]) if row["entity_id"] else None,
        company_id=UUID(row["company_id"]) if row["company_id"] else None,
        kind=row["kind"],
        source=row["source"],
        source_url=row["source_url"],
        observed_at=db.from_iso(row["observed_at"]),
        ingested_at=db.from_iso(row["ingested_at"]),
        payload=json.loads(row["payload"]),
        evidence_span=row["evidence_span"],
        confidence=row["confidence"],
        integrity_flags=json.loads(row["integrity_flags"]),
    )


# ---------------------------------------------------------------------------
# Entity / company rows. Not events — these are the identities events point at.
# ---------------------------------------------------------------------------


def upsert_entity(name: str, normalized: str) -> UUID:
    """Keyed on the normalized name — that is what the resolver matches on."""
    conn = db.connect()
    row = conn.execute(
        "select entity_id from entities where name_normalized = ?", (normalized,)
    ).fetchone()
    if row:
        return UUID(row["entity_id"])
    entity_id = uuid4()
    conn.execute(
        "insert into entities (entity_id, display_name, name_normalized, created_at) "
        "values (?,?,?,?)",
        (str(entity_id), name, normalized, db.to_iso(utcnow())),
    )
    conn.commit()
    return entity_id


def upsert_company(name: str, archetype: int | None = None) -> UUID:
    conn = db.connect()
    row = conn.execute("select company_id from companies where name = ?", (name,)).fetchone()
    if row:
        return UUID(row["company_id"])
    company_id = uuid4()
    conn.execute(
        "insert into companies (company_id, name, archetype, created_at) values (?,?,?,?)",
        (str(company_id), name, archetype, db.to_iso(utcnow())),
    )
    conn.commit()
    return company_id


def get_entity(entity_id: UUID) -> dict | None:
    row = db.connect().execute(
        "select * from entities where entity_id = ?", (str(entity_id),)
    ).fetchone()
    return dict(row) if row else None


def get_company(company_id: UUID) -> dict | None:
    row = db.connect().execute(
        "select * from companies where company_id = ?", (str(company_id),)
    ).fetchone()
    return dict(row) if row else None


def all_entities() -> list[dict]:
    return [dict(r) for r in db.connect().execute("select * from entities order by created_at")]


def all_companies() -> list[dict]:
    return [dict(r) for r in db.connect().execute("select * from companies order by created_at")]
