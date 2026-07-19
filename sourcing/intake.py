"""Inbound applications: a company name plus a deck, in through the one funnel. Owner: B.

S1 is "inbound (deck+name) + outbound (scanners + PPR graph diffusion) -> activate ->
ONE FUNNEL". This module is the inbound half and the point where the two converge.

Three things it deliberately refuses to do:

  1. **Read the PDF itself.** Every byte of founder-supplied text goes through
     sourcing/deck.py, which goes through sourcing/bus.py — that is what applies the
     sanitizer, keeps a slide id on every span, and stamps observed_at from the deck's
     own clock. scripts/seed.py bypassed the bus once and the result was a live prompt
     injection sitting unsanitized in the store. There is no second path in here, and
     `extract` is injected only so tests can substitute a fake FUNNEL, never a bypass.

  2. **Store a status.** See `status()`. Every stage is re-derived on each read from the
     event log, the gate and the outbound tables. A stored status is a status that
     drifts the first moment a stage fails silently — and the stage most likely to fail
     silently is the one nobody is watching.

  3. **Match company names itself.** Convergence calls memory/resolver.py at resolver's
     own thresholds. The plan's Type 1 guarded failure is "double-count on in/outbound
     merge"; a second matcher in this file would be a second answer to that question.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import UUID, uuid4

from memory import resolver, store
from schema.events import (
    CompanyProvenance,
    EntityCandidate,
    Event,
    EventKind,
    ResolutionStatus,
    Source,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intake limits. Rejections here are the ones a founder sees, so they say why.
# ---------------------------------------------------------------------------

PDF_MAGIC = b"%PDF-"
MAX_DECK_BYTES = 25 * 1024 * 1024  # a pitch deck; anything larger is not one
MAX_NAME_CHARS = 200

# The funnel stages, in order. A stage cannot be reached before the one before it —
# see `status()`. These are names for facts, not values anybody assigns.
RECEIVED = "received"
INGESTED = "ingested"
SCREENED = "screened"
GATED = "gated"
DECIDED = "decided"
STAGES = (RECEIVED, INGESTED, SCREENED, GATED, DECIDED)

# Events that only exist because the intelligence layer looked at this company. Their
# presence in the log IS the fact that screening ran; there is no "screened" boolean.
SCREENING_KINDS = frozenset(
    {
        EventKind.VALIDATION_RESULT,
        EventKind.CONTRADICTION,
        EventKind.GREEN_FLAG,
        EventKind.PROOF_CHALLENGE_ISSUED,
        EventKind.PROOF_ARTIFACT,
        EventKind.PROOF_BEHAVIOR,
    }
)

# Legal-form suffixes are packaging, not identity. Stripping them is NORMALIZATION and
# happens before the resolver is asked; the comparison itself is still the resolver's.
_SUFFIX_RE = re.compile(
    r"\b(inc|inc\.|llc|ltd|ltd\.|limited|corp|corp\.|corporation|co|gmbh|bv|ab|oy|plc|"
    r"pty|sarl|srl|spa|ag|as|kk|pbc)\b\.?\s*$",
    re.IGNORECASE,
)


class Rejected(ValueError):
    """An upload we will not accept, with a reason a human can act on.

    Raised before anything is written or parsed. The founder gets the sentence; nobody
    gets a stack trace.
    """


# ---------------------------------------------------------------------------
# Storage. Same shape as sourcing/outreach.py: SQLite DDL here, Postgres via 004.
# ---------------------------------------------------------------------------

SQLITE_SCHEMA = """
create table if not exists applications (
    application_id    text primary key,
    company_id        text not null,
    submitted_name    text not null,
    company_name      text not null,
    submitted_by      text,
    founder_name      text,
    founder_email     text,
    founder_entity_id text,
    deck_filename     text not null,
    deck_sha256       text not null,
    deck_bytes        integer not null,
    deck_path         text not null,
    convergence       text not null default '{}',
    received_at       text not null
);

create index if not exists idx_applications_company on applications (company_id);
create unique index if not exists idx_applications_dedupe
    on applications (company_id, deck_sha256);
"""

_ensured: dict[int, Any] = {}


def conn() -> Any:
    """Shared connection with the applications table guaranteed to exist.

    Mirrors outreach.conn(): on Postgres the table arrives via migration 004, which
    db.connect() applies on first connect; on SQLite there are no migrations, so the
    DDL runs here, keyed by connection identity so a test repointing VCBRAIN_DB_PATH
    re-ensures against the new file.
    """
    from memory import db

    c = db.connect()
    if _ensured.get(id(c)) is not c and db.backend() == db.SQLITE:
        c.executescript(SQLITE_SCHEMA)
        c.commit()
        _ensured[id(c)] = c
    return c


def _write(sql: str, args: tuple | list = ()) -> None:
    c = conn()
    c.execute(sql, args)
    c.commit()


def _fetch(sql: str, args: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime | None:
    from sourcing import bus

    return bus.parse_ts(value)


def deck_dir() -> Path:
    """Where uploaded decks land. VCBRAIN_DECK_DIR lets tests use a tmp path.

    Defaults under core.config.cache_root(), which is `data/` locally and `/tmp` on the
    read-only serverless filesystem. The BINARY IS DELIBERATELY NOT DURABLE STATE: it is
    written because the reader in sourcing/deck.py extracts from a path, and after that
    nothing here opens it again (`deck_path` is recorded as provenance and never read).
    What survives is what migration 004 says survives — every claim and integrity finding
    from the deck is an event in Postgres, and the dedupe key is `deck_sha256`, not the
    file. So a deck that evaporates with the lambda costs us nothing we rely on.
    """
    from core.config import cache_root

    return Path(os.getenv("VCBRAIN_DECK_DIR") or cache_root() / "decks")


# ---------------------------------------------------------------------------
# Validation. Everything here runs BEFORE a byte is written or parsed.
# ---------------------------------------------------------------------------


def validate(company_name: str, filename: str | None, content: bytes) -> tuple[str, str]:
    """Return (clean name, clean filename) or raise Rejected with the reason.

    The magic-byte check is the real one. A `.pdf` extension is a claim made by whoever
    is uploading, and this is the most hostile input the system accepts.
    """
    name = (company_name or "").strip()
    if not name:
        raise Rejected("company name is required")
    if len(name) > MAX_NAME_CHARS:
        raise Rejected(f"company name is longer than {MAX_NAME_CHARS} characters")

    clean_file = Path((filename or "deck.pdf").strip()).name or "deck.pdf"
    if not content:
        raise Rejected("the uploaded file is empty")
    if len(content) > MAX_DECK_BYTES:
        raise Rejected(
            f"deck is {len(content) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_DECK_BYTES // 1_048_576} MB"
        )
    if not content.startswith(PDF_MAGIC):
        raise Rejected(
            f"{clean_file} is not a PDF — the file does not begin with %PDF-. "
            "Decks must be uploaded as PDF."
        )
    return name, clean_file


# ---------------------------------------------------------------------------
# Convergence. The company a submission belongs to, decided by memory/resolver.py.
# ---------------------------------------------------------------------------


def _normalize_company(name: str) -> str:
    """resolver's normalizer, minus the legal suffix. Not a similarity function."""
    return resolver.normalize_name(_SUFFIX_RE.sub("", name.strip())).strip()


def converge_company(submitted_name: str) -> dict:
    """Which company row this submission is about.

    MERGED  -> an existing company; the inbound application attaches to it and there is
               no second record. This is the "we emailed them before they applied" case.
    AMBIGUOUS -> similar but under resolver's merge threshold. We keep a SEPARATE record
               and say so, rather than guessing two companies are one.
    NEW     -> nothing close.

    The comparison is resolver.name_similarity at resolver's own thresholds. This
    function does not own a number.
    """
    normalized = _normalize_company(submitted_name)
    scored: list[tuple[float, dict]] = []
    for row in store.all_companies():
        existing = str(row.get("name") or "")
        if not existing:
            continue
        if _normalize_company(existing) == normalized:
            scored.append((1.0, row))
            continue
        scored.append((resolver.name_similarity(submitted_name, existing), row))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("company_id"))))

    best_score, best = scored[0] if scored else (0.0, None)
    alternatives = [
        {"company_id": str(r.get("company_id")), "name": r.get("name"), "score": round(s, 3)}
        for s, r in scored[1:4]
        if s >= resolver.NEW_THRESHOLD
    ]

    if best is not None and best_score >= resolver.MERGE_THRESHOLD:
        company_id = UUID(str(best["company_id"]))
        return {
            "status": ResolutionStatus.MERGED.value,
            "company_id": str(company_id),
            "company_name": best.get("name"),
            "score": round(best_score, 3),
            "matched_name": best.get("name"),
            "alternatives": alternatives,
            "rationale": (
                f"name similarity {best_score:.2f} against the existing record "
                f"{best.get('name')!r}, at or above resolver.MERGE_THRESHOLD "
                f"({resolver.MERGE_THRESHOLD}) — one record, not two"
            ),
        }

    # An inbound application is a real founder submitting real material, so this is
    # genuinely SOURCED. Stated explicitly because the default was removed: a silent
    # claim of evidence-backing is the failure this field exists to prevent.
    company_id = store.upsert_company(
        submitted_name, provenance=CompanyProvenance.SOURCED
    )
    if best is not None and best_score >= resolver.NEW_THRESHOLD:
        return {
            "status": ResolutionStatus.AMBIGUOUS.value,
            "company_id": str(company_id),
            "company_name": submitted_name,
            "score": round(best_score, 3),
            "matched_name": best.get("name"),
            "alternatives": alternatives,
            "rationale": (
                f"name similarity {best_score:.2f} against {best.get('name')!r} falls in "
                f"resolver's {resolver.NEW_THRESHOLD}–{resolver.MERGE_THRESHOLD} band. "
                "We could not confirm these are the same company, so they are kept "
                "separate and surfaced rather than merged on a guess"
            ),
        }
    return {
        "status": ResolutionStatus.NEW.value,
        "company_id": str(company_id),
        "company_name": submitted_name,
        "score": round(best_score, 3),
        "matched_name": best.get("name") if best is not None else None,
        "alternatives": alternatives,
        "rationale": "no existing company matched — new record",
    }


def _resolve_founder(name: str | None, email: str | None, source_url: str | None) -> dict | None:
    """The submitting founder, through the real resolver. None when nothing was given.

    A deck upload with a contact email is one of the few places this system gets a
    strong identifier, so it is worth spending the resolver call: it is what makes the
    inbound person the SAME person the outbound scanners already scored.
    """
    if not (name or "").strip() and not (email or "").strip():
        return None
    resolution = resolver.resolve(
        EntityCandidate(
            name=(name or "").strip() or None,
            email=(email or "").strip() or None,
            urls=[source_url] if source_url else [],
            source=Source.DECK,
        )
    )
    return {
        "status": resolution.status.value,
        "entity_id": str(resolution.entity_id),
        "score": resolution.score,
        "rationale": resolution.rationale,
        "signals": resolution.signals,
        "alternatives": [str(a) for a in resolution.alternatives],
    }


# ---------------------------------------------------------------------------
# Submit. The only write path.
# ---------------------------------------------------------------------------


def submit(
    company_name: str,
    content: bytes,
    *,
    filename: str | None = None,
    submitted_by: str | None = None,
    founder_name: str | None = None,
    founder_email: str | None = None,
    extract: Callable[..., list[Event]] | None = None,
) -> dict:
    """Accept an application: validate, converge, run the deck through the funnel.

    `extract` defaults to sourcing.deck.extract and exists so a test can assert the
    funnel was called with the right arguments. Substituting it does not create a
    bypass — nothing in this module reads the PDF on its own.
    """
    name, clean_file = validate(company_name, filename, content)
    digest = hashlib.sha256(content).hexdigest()

    convergence = converge_company(name)
    company_id = UUID(convergence["company_id"])

    # Re-uploading the same bytes for the same company is one application, not two.
    # The other half of the double-count guard; convergence handles the name half.
    existing = _fetch(
        "select * from applications where company_id = ? and deck_sha256 = ?",
        (str(company_id), digest),
    )
    if existing:
        row = _hydrate(existing[0])
        row["duplicate_of"] = row["application_id"]
        row["note"] = "this exact deck was already received for this company"
        return row

    application_id = uuid4()
    path = deck_dir() / f"{application_id}.pdf"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as exc:
        # Unlike a cache write, this one cannot be skipped — the deck reader takes a path,
        # so there is no funnel without it. Say so instead of raising OSError into a 500:
        # the applicant learns their deck was not accepted rather than watching a spinner
        # end in a stack trace, and nothing has been recorded as received.
        raise Rejected(
            "the deck could not be stored for processing, so it was not accepted — "
            "nothing was recorded; please retry"
        ) from exc

    # THE FUNNEL. deck.extract -> bus.prepare -> sanitize. Sanitization, slide ids and
    # observed_at all happen in there, and none of them happen out here.
    runner = extract or _default_extract
    try:
        events = runner(path, company_id)
    except Rejected:
        raise
    except Exception as exc:  # noqa: BLE001 — a corrupt PDF is a rejection, not a 500
        path.unlink(missing_ok=True)
        raise Rejected(f"the deck could not be read as a PDF ({type(exc).__name__})") from exc

    founder = _resolve_founder(founder_name, founder_email, str(path))
    entity_id = UUID(founder["entity_id"]) if founder else None
    for event in events:
        if entity_id is not None and event.entity_id is None:
            event = event.model_copy(update={"entity_id": entity_id})
        store.append(event)

    received_at = _now()
    _write(
        "insert into applications (application_id, company_id, submitted_name, company_name, "
        "submitted_by, founder_name, founder_email, founder_entity_id, deck_filename, "
        "deck_sha256, deck_bytes, deck_path, convergence, received_at) "
        "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(application_id),
            str(company_id),
            name,
            convergence.get("company_name") or name,
            submitted_by,
            founder_name,
            founder_email,
            str(entity_id) if entity_id else None,
            clean_file,
            digest,
            len(content),
            str(path),
            json.dumps({**convergence, "founder": founder}),
            _iso(received_at),
        ),
    )
    row = get(application_id)
    assert row is not None  # just inserted
    row["events"] = _event_summary(events)
    return row


def _default_extract(path: Path, company_id: UUID) -> list[Event]:
    from sourcing import deck

    return deck.extract(path, company_id)


def _event_summary(events: Iterable[Event]) -> dict:
    events = list(events)
    return {
        "count": len(events),
        "by_kind": {
            kind: sum(1 for e in events if e.kind == kind)
            for kind in sorted({str(e.kind) for e in events})
        },
        "integrity_flags": sorted({f for e in events for f in e.integrity_flags}),
        "items": [
            {
                "event_id": str(e.event_id),
                "kind": str(e.kind),
                "observed_at": _iso(e.observed_at),
                "evidence_span": e.evidence_span,
                "slide": e.payload.get("slide"),
                "confidence": e.confidence,
                "integrity_flags": e.integrity_flags,
            }
            for e in events
        ],
    }


# ---------------------------------------------------------------------------
# Read paths.
# ---------------------------------------------------------------------------


def _hydrate(row: dict) -> dict:
    out = dict(row)
    value = out.get("convergence")
    if isinstance(value, str):
        try:
            out["convergence"] = json.loads(value)
        except ValueError:
            out["convergence"] = None
    return out


def get(application_id: UUID | str) -> dict | None:
    rows = _fetch("select * from applications where application_id = ?", (str(application_id),))
    return _hydrate(rows[0]) if rows else None


def applications(company_id: UUID | str | None = None) -> list[dict]:
    """Every application, newest first."""
    if company_id is None:
        return [_hydrate(r) for r in _fetch("select * from applications order by received_at desc")]
    return [
        _hydrate(r)
        for r in _fetch(
            "select * from applications where company_id = ? order by received_at desc",
            (str(company_id),),
        )
    ]


def _events_recorded_before(company_id: UUID, inbound_at: datetime) -> int:
    """How many non-deck events we had ALREADY RECORDED when the application landed.

    THE ONE SANCTIONED READ OF `ingested_at` AS A PREDICATE, and it is deliberately in a
    named function so it is greppable and so the exception has somewhere to be argued.
    `SHARED.md` and `schema/events.py` both say `ingested_at` is NEVER used in scoring.
    That rule is intact: nothing here reaches a score, a rank, an axis or a cheque. This
    returns a count for one narrative field on the arrival panel.

    WHY THE EXCEPTION IS LEGITIMATE. The question is "did WE find them before they
    applied to us". That is a fact about our own pipeline's clock, not a fact about the
    founder or the world — and `ingested_at` is the only column that records it.
    `observed_at` cannot answer it and would silently answer a different question: a
    GitHub commit from 2019 has `observed_at` in 2019, which precedes every application
    ever, so every company would trivially qualify as "we found them first" and the
    field would read as an impressive number that means nothing. The `ingested_at`
    version can actually be false, which is what makes it worth showing.

    WHY IT CANNOT LEAK. The return value is an int that flows only into
    `arrival()["events_recorded_before_application"]` and the `paths` list beside it.
    `tests/test_applications.py` pins that nothing on this path reaches the scorer. If
    you ever want this number inside a score, that is a different decision than this
    one, and it needs its own argument — the invariant exists because "when we happened
    to look" is not evidence about a founder, and a founder cannot act on it.

    `as_of` is `inbound_at`, not `_now()`. The unscoped read was wall-clock dependent
    for no reason: an event whose `observed_at` is AFTER the application cannot be
    evidence that we found them first, whatever its ingest time, so scoping the window
    here makes the count answer its own question and makes the result reproducible.
    """
    return sum(
        1
        for e in store.events(as_of=inbound_at, company_id=company_id)
        if e.source != Source.DECK and e.ingested_at < inbound_at
    )


def arrival(company_id: UUID | str, *, received_at: datetime | None = None) -> dict:
    """Which path(s) this company arrived by. Derived, never recorded on the company.

    "We emailed them before they applied" is a demo beat, so it has to be true: it is
    computed by comparing the first outbound draft's created_at against the first
    application's received_at, both of which are timestamps someone else wrote.
    """
    from sourcing import outreach

    cid = str(company_id)
    apps = applications(cid)
    inbound_at = min(
        (d for d in (_parse(a.get("received_at")) for a in apps) if d), default=received_at
    )

    try:
        drafts = outreach.history(cid)
    except Exception as exc:  # noqa: BLE001 — outbound being unavailable is not inbound's failure
        log.info("outbound history unavailable (%s): %s", type(exc).__name__, exc)
        drafts = []
    outbound_at = min((d for d in (_parse(r.get("created_at")) for r in drafts) if d), default=None)

    # Broader than "we drafted mail": any non-deck event we had recorded before the
    # application landed means the scanners or the graph had already found them.
    found_first = 0
    if inbound_at is not None:
        cid_uuid = _as_uuid(cid)
        if cid_uuid is not None:
            found_first = _events_recorded_before(cid_uuid, inbound_at)

    paths = []
    if drafts or found_first:
        paths.append("outbound")
    if apps:
        paths.append("inbound")

    return {
        "paths": paths,
        "inbound_applications": len(apps),
        "outbound_drafts": len(drafts),
        "first_inbound_at": _iso(inbound_at) if inbound_at else None,
        "first_outbound_at": _iso(outbound_at) if outbound_at else None,
        "events_recorded_before_application": found_first,
        "outbound_preceded_inbound": bool(outbound_at and inbound_at and outbound_at < inbound_at),
        "converged": len(paths) > 1,
    }


def _as_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# THE FUNNEL STATUS. Derived on every read. Nothing below writes anything.
# ---------------------------------------------------------------------------


def _stage(name: str, reached: bool, at: datetime | None, because: str, **extra: Any) -> dict:
    return {
        "stage": name,
        "reached": reached,
        "at": _iso(at) if at else None,
        "because": because,
        **extra,
    }


def status(application_id: UUID | str, *, as_of: datetime | None = None) -> dict:
    """received -> ingested -> screened -> gated -> decided, recomputed from facts.

    There is no status column and no setter. Each stage answers a question about
    something another part of the system actually did:

      received  the application row exists, with the timestamp of the upload
      ingested  deck events for this company are in the log (with their slide ids)
      screened  the intelligence layer left VALIDATION_RESULT / CONTRADICTION / PROOF_*
                events on this company — running is what writes them
      gated     intelligence.gate.evaluate returns a decision over that evidence
      decided   the gate returned PROCEED or NO_CALL, or a human dispositioned an
                outbound draft. PROOF_PROTOCOL is explicitly NOT decided: the system
                asked for more evidence, which is the opposite of a decision.

    Stages are monotone — a stage is only `reached` if every stage before it is. A
    funnel that reports "gated" over an ingestion that produced nothing is exactly the
    drift a stored status produces, and it is the failure this shape is chosen to avoid.
    """
    row = get(application_id)
    if row is None:
        raise LookupError(f"no such application: {application_id}")

    cutoff = as_of or _now()
    company_id = _as_uuid(row["company_id"])
    received_at = _parse(row.get("received_at"))
    events = store.events(as_of=cutoff, company_id=company_id) if company_id else []

    stages = [
        _stage(
            RECEIVED,
            True,
            received_at,
            f"application {row['application_id']} was accepted with "
            f"{row['deck_bytes']} bytes of PDF (sha256 {row['deck_sha256'][:12]})",
        )
    ]

    deck_events = [e for e in events if e.source == Source.DECK]
    claims = [e for e in deck_events if e.kind == EventKind.DECK_CLAIM]
    integrity = [e for e in deck_events if e.kind == EventKind.INTEGRITY]
    stages.append(
        _stage(
            INGESTED,
            bool(deck_events),
            max((e.ingested_at for e in deck_events), default=None),
            (
                f"{len(deck_events)} deck events in the log "
                f"({len(claims)} claims, {len(integrity)} integrity)"
                if deck_events
                else "no deck events in the log for this company"
            ),
            event_count=len(deck_events),
            claim_count=len(claims),
            integrity_count=len(integrity),
            slides=sorted({e.payload["slide"] for e in deck_events if e.payload.get("slide")}),
        )
    )

    screened = [e for e in events if e.kind in SCREENING_KINDS]
    stages.append(
        _stage(
            SCREENED,
            bool(screened),
            max((e.ingested_at for e in screened), default=None),
            (
                f"{len(screened)} screening events "
                f"({', '.join(sorted({str(e.kind) for e in screened}))})"
                if screened
                else "the intelligence layer has not written a screening event for this company yet"
            ),
            event_count=len(screened),
        )
    )

    decision, gate_error = _gate(company_id, cutoff) if company_id else (None, "no company")
    stages.append(
        _stage(
            GATED,
            decision is not None,
            cutoff if decision is not None else None,
            (
                f"gate returned {decision.outcome.value.upper()}: {decision.rationale}"
                if decision is not None
                else f"the gate could not be evaluated ({gate_error})"
            ),
            outcome=decision.outcome.value if decision is not None else None,
            absence_is_suspicious=(
                decision.absence_is_suspicious if decision is not None else None
            ),
        )
    )

    call = decision.outcome.value if decision is not None else None
    terminal = call in ("proceed", "no_call")
    human = _human_disposition(row["company_id"])
    stages.append(
        _stage(
            DECIDED,
            bool(terminal or human),
            _parse(human.get("decided_at")) if human else (cutoff if terminal else None),
            (
                f"a human recorded {human['status']} on outbound draft "
                f"{human['draft_id']}" + (f" (gate: {call.upper()})" if call else "")
                if human
                else f"gate returned {call.upper()}, which is a call"
                if terminal
                else "the gate asked for more evidence (PROOF_PROTOCOL); "
                "that is a request, not a decision"
                if call
                else "nothing has decided yet"
            ),
            call=call,
            human=human,
        )
    )

    # Monotone: the funnel is only as far along as its first unreached stage.
    current = RECEIVED
    for stage in stages:
        if not stage["reached"]:
            stage["reached"] = False
            break
        current = stage["stage"]
    for stage in stages[STAGES.index(current) + 1 :]:
        if stage["reached"]:
            stage["reached"] = False
            stage["blocked_by"] = current
            stage["because"] = (
                f"held behind '{current}' — a later stage cannot be reported as reached "
                f"over an earlier one that is not. ({stage['because']})"
            )

    return {
        "application_id": row["application_id"],
        "company_id": row["company_id"],
        "company_name": row["company_name"],
        "as_of": _iso(cutoff),
        "status": current,
        "stages": stages,
        "arrival": arrival(row["company_id"], received_at=received_at),
        "derivation": (
            "Nothing in this payload is stored. Every stage is recomputed from the event "
            "log, the gate and the outbound tables on each read, so a stage that silently "
            "stopped happening reads as not reached instead of staying green."
        ),
    }


def _gate(company_id: UUID, as_of: datetime) -> tuple[Any, str]:
    """The gate's own decision over the log, or the reason we have not got one.

    Not reached at all when there is no evidence: a gate asked to judge an empty log
    returns a value, and reporting that as a real decision is the drift this whole
    module is shaped to avoid.
    """
    if not store.events(as_of=as_of, company_id=company_id):
        return None, "no events for this company yet"
    try:
        from intelligence import gate

        return gate.evaluate(company_id, as_of), ""
    except Exception as exc:  # noqa: BLE001 — an unavailable gate is not a gated application
        log.info("gate unavailable (%s): %s", type(exc).__name__, exc)
        return None, f"{type(exc).__name__}: {exc}"


def _human_disposition(company_id: str) -> dict | None:
    """The most recent outbound draft a person actually decided on, if any."""
    from sourcing import outreach

    try:
        drafts = outreach.history(company_id)
    except Exception as exc:  # noqa: BLE001
        log.info("outbound history unavailable (%s): %s", type(exc).__name__, exc)
        return None
    decided = [
        d
        for d in drafts
        if d.get("decided_at") and d.get("status") in (outreach.APPROVED, outreach.REJECTED)
    ]
    if not decided:
        return None
    latest = max(decided, key=lambda d: str(d["decided_at"]))
    return {
        "draft_id": str(latest["draft_id"]),
        "status": latest["status"],
        "decided_at": latest["decided_at"],
        "decided_by": latest.get("decided_by"),
    }
