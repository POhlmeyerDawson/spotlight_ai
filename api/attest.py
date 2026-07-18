"""Attestation for Proof Protocol submissions. Owner: D.

The behavioural trace is the sharpest signal in the system — pushing back on the
planted bad constraint is worth half the behavioural score. It was also, until
this module, entirely client-supplied: a founder could POST
`{"pushed_back_on_constraint": true}` and buy the strongest signal we have.

The rule here is the one the validator already applies to claims: a self-reported
assertion is never evidence. So we split the trace in two —

  ATTESTED     fields the server observed itself (issue time, submission time,
               elapsed, and any commits fetched from a public repo)
  SELF_REPORTED everything the client asserted

— and mark the difference on the emitted events, so the score, the memo and the
UI all know which half they are looking at. We do not silently discard the
self-reported half; we refuse to let it masquerade as observed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from schema.events import utcnow

# Fields a founder cannot be trusted to report about themselves. Each is either
# replaced by a server observation or carried through explicitly marked.
SELF_REPORTABLE = (
    "pushed_back_on_constraint",
    "questions_asked",
    "commits",
    "started_at",
    "submitted_at",
    "iterations",
)

# Server-side record of when each challenge was issued AND who it was issued to.
# In-process is fine for a demo; the point is that the anchor is ours, not the
# client's. The company is recorded so a submission cannot be graded against a
# different company than the challenge was written for.
_ISSUED: dict[str, tuple[datetime, str | None]] = {}


def record_issue(
    challenge_id: str, issued_at: datetime | None = None, company_id: str | None = None
) -> None:
    _ISSUED[str(challenge_id)] = (issued_at or utcnow(), str(company_id) if company_id else None)


def issued_at(challenge_id: str) -> datetime | None:
    rec = _ISSUED.get(str(challenge_id))
    return rec[0] if rec else None


def issued_company(challenge_id: str) -> str | None:
    rec = _ISSUED.get(str(challenge_id))
    return rec[1] if rec else None


def reset() -> None:
    """Test/demo hook."""
    _ISSUED.clear()


def challenge_belongs_to(challenge_id: str, company_id: UUID | None) -> bool | None:
    """Does this challenge belong to this company? None when we cannot tell.

    Checked against our own issue record first, then the PROOF_CHALLENGE_ISSUED
    event. Without this, a submission for an easy company's challenge could be
    graded onto a different company's founder score.
    """
    if company_id is None:
        return None

    recorded = issued_company(challenge_id)
    if recorded is not None:
        return recorded == str(company_id)

    try:
        from memory import store
        from schema.events import EventKind

        cid = uuid_or_none(challenge_id)
        for ev in store.events(as_of=utcnow(), kind=str(EventKind.PROOF_CHALLENGE_ISSUED)):
            if str(ev.payload.get("challenge_id")) == str(challenge_id) or ev.event_id == cid:
                return ev.company_id == company_id
    except Exception:  # noqa: BLE001 - unknown provenance is not a mismatch
        return None
    return None


def attest(
    challenge_id: str,
    trace: dict,
    *,
    repo_url: str | None = None,
    demo: bool = False,
) -> tuple[dict, dict]:
    """Return (trace_for_grading, attestation).

    trace_for_grading keeps the client's fields — the grader still needs them —
    but every server-observable value is overwritten with what we actually saw,
    and the attestation travels INSIDE the trace so the grader can weight
    self-reported behaviour differently rather than only having it corrected
    after the fact.
    """
    now = utcnow()
    issued = issued_at(challenge_id)

    observed: dict[str, Any] = {"submitted_at": now.isoformat()}
    attested: list[str] = ["submitted_at"]

    if issued is not None:
        observed["started_at"] = issued.isoformat()
        observed["elapsed_seconds"] = round((now - issued).total_seconds(), 1)
        attested += ["started_at", "elapsed_seconds"]

    fetched = _fetch_commits(repo_url) if repo_url else None
    if fetched is not None:
        observed["commits"] = fetched
        attested.append("commits")

    merged = {**(trace or {}), **observed}
    self_reported = [f for f in SELF_REPORTABLE if f in (trace or {}) and f not in attested]

    attestation = {
        "challenge_anchored": issued is not None,
        "attested_fields": attested,
        "self_reported_fields": self_reported,
        # A trace with no server-side anchor cannot be placed in time at all. It is
        # still graded — refusing outright would punish a founder for our plumbing —
        # but it never counts as observed behaviour.
        "trust": _trust(issued is not None, bool(fetched), self_reported, demo),
        "demo_seeded": demo,
        "note": _note(issued is not None, bool(fetched), self_reported, demo),
    }
    # The grader needs this BEFORE scoring, not just afterwards: a self-reported
    # pushback must not earn the same scalar as an observed one. Post-grade
    # confidence scaling stays as a second line of defence.
    merged["attestation"] = attestation
    return merged, attestation


def _trust(anchored: bool, has_repo: bool, self_reported: list[str], demo: bool) -> float:
    if demo:
        return 0.5  # seeded for the stage; honest about it rather than scored as real
    trust = 0.35
    if anchored:
        trust += 0.25
    if has_repo:
        trust += 0.3
    if not self_reported:
        trust += 0.1
    return round(min(trust, 1.0), 2)


def _note(anchored: bool, has_repo: bool, self_reported: list[str], demo: bool) -> str:
    if demo:
        return "Seeded demonstration completion. The machinery is real; this run was pre-recorded."
    parts = []
    parts.append(
        "Timing observed server-side."
        if anchored
        else "No server-side issue record — timing is unverified."
    )
    if has_repo:
        parts.append("Commits fetched independently from the public repository.")
    if self_reported:
        parts.append(
            "Self-reported and not independently observed: " + ", ".join(self_reported) + "."
        )
    return " ".join(parts)


def _fetch_commits(repo_url: str) -> list[dict] | None:
    """Commits read from the public repo by us, not asserted by the submitter."""
    try:
        from sourcing.scanners import github

        signals = github.scan(repo_url, limit=50)
        commits = [
            {
                "at": s.meta.get("observed_at") or s.fetched_at.isoformat(),
                "message": s.meta.get("message", ""),
                "files": s.meta.get("files", 0),
            }
            for s in signals
            if s.meta.get("kind") in ("commit", "repo_activity")
        ]
        return commits or None
    except Exception:  # noqa: BLE001 - an unreachable repo is unattested, not an error
        return None


def apply(events: list, attestation: dict) -> list:
    """Stamp attestation onto graded events and scale their confidence by its trust.

    The filter treats PROOF_* events as low-noise, high-weight observations. An
    unattested trace must not buy that weight, so trust multiplies confidence
    rather than sitting beside it as a decorative field.
    """
    trust = float(attestation.get("trust", 0.35))
    for ev in events or []:
        ev.payload = {**(ev.payload or {}), "attestation": attestation}
        ev.confidence = round(min(1.0, max(0.0, ev.confidence * trust)), 3)
        if attestation.get("self_reported_fields") and "unattested_trace" not in ev.integrity_flags:
            ev.integrity_flags = [*ev.integrity_flags, "unattested_trace"]
    return events


def uuid_or_none(v: str) -> UUID | None:
    try:
        return UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        return None
