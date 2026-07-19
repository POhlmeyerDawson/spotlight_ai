"""Outbound cold-reach: eligibility, drafting, the review queue, suppression.

Owner: B. DIFFERENTIATOR §6. Thin — every decision lives in sourcing/outreach.py.

THERE IS NO SEND ENDPOINT AND THERE IS NO EMAIL PROVIDER IN THIS CHANGE. `approve`
records that a human is willing to send; the send is that human's act, performed
outside this system. That is not an unfinished corner of the feature, it is the feature:
the system is asserting things about a person, to that person, and a queue that a
scheduler can drain is a queue that will eventually be drained by a scheduler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from api.auth import optional_user
from api.routers.deps import company_uuid, fixture_key, resolve_as_of
from schema.vc import User
from sourcing import outreach

router = APIRouter(prefix="/outbound", tags=["outbound"])


def _resolve(company_id: str) -> UUID:
    """Slug or UUID -> a company that actually EXISTS in the store.

    company_uuid alone passes any well-formed UUID straight through, so without the
    existence check a typo'd id would reach the drafting path and fail there with a
    message about evidence rather than about the id.
    """
    from memory import store

    cid = company_uuid(company_id)
    if cid is None or store.get_company(cid) is None:
        raise HTTPException(404, f"unknown company: {company_id}")
    return cid


def _red_lines(user: User | None) -> list:
    """The signed-in VC's red lines, or none. An outage here must not silently RELAX the
    gate — a failure to load red lines is treated as a failure to clear them."""
    if user is None:
        return []
    from memory import profiles

    try:
        return profiles.get_profile(user.user_id).derived.red_lines
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            503,
            "the VC profile could not be loaded, so its red lines could not be checked. "
            f"Outbound is blocked rather than sent unchecked ({exc})",
        ) from exc


@router.get("/eligible")
def get_eligible(
    as_of: Optional[datetime] = None,
    company_id: Optional[str] = None,
    user: User | None = Depends(optional_user),
) -> dict:
    """Who genuinely passes, and — for everyone else — exactly which check said no.

    The ineligible list is returned in full and is the more useful half of this payload.
    A funnel that reports only its survivors cannot be audited for being too wide, and
    "too wide" is the only way this feature fails badly.
    """
    cutoff = resolve_as_of(as_of)
    red_lines = _red_lines(user)

    from memory import store
    from api.routers.deps import backtest_cohort_names, prior_company_names

    if company_id is not None:
        rows = [{"company_id": str(_resolve(company_id))}]
    else:
        # Same exclusions the ranked list applies: a serial founder's PRIOR company and
        # the backtest replay cohort are history, not people to cold-email in 2026.
        excluded = prior_company_names() | backtest_cohort_names()
        rows = [r for r in store.all_companies() if r.get("name") not in excluded]

    eligible, ineligible = [], []
    for row in rows:
        cid = company_uuid(str(row["company_id"]))
        if cid is None:
            continue
        verdict = outreach.eligibility(cid, cutoff, red_lines=red_lines)
        verdict["id"] = fixture_key(str(cid))
        (eligible if verdict["eligible"] else ineligible).append(verdict)

    return {
        "as_of": cutoff.isoformat(),
        "profile_active": user is not None,
        "eligible": eligible,
        "ineligible": ineligible,
        "rule": (
            "A company is eligible only when the system independently decided in its "
            "favour on every count: the gate returned PROCEED, the investment "
            "recommendation returned an actual amount rather than a refusal, no deck "
            "claim was CONTRADICTED, no event carries an impeaching integrity flag, no "
            "stated red line on the active profile is unresolved, and the company is not "
            "suppressed. There is no threshold in this feature that anyone typed."
        ),
    }


@router.post("/draft/{company_id}")
def post_draft(
    company_id: str,
    as_of: Optional[datetime] = None,
    recipient_email: Optional[str] = Query(default=None),
    user: User | None = Depends(optional_user),
) -> dict:
    """Draft from the evidence trace, verify, queue. 422 when it cannot be verified.

    A 422 here means the generated text could not be grounded in stored evidence and was
    DISCARDED — it is recorded with status `rejected_unverifiable` and is not in the
    queue. The caller gets the reason so a retry is possible; no human is ever offered
    the unverifiable text.
    """
    try:
        return outreach.draft(
            _resolve(company_id),
            resolve_as_of(as_of),
            red_lines=_red_lines(user),
            recipient_email=recipient_email,
        )
    except outreach.Unverifiable as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/queue")
def get_queue(status: str = Query(default=outreach.QUEUED)) -> dict:
    """Drafts a human may act on.

    `rejected_unverifiable` is readable here only if asked for BY NAME, for auditing.
    It is never in the default listing, so it cannot be approved by someone working
    down the page.
    """
    if status not in (
        outreach.QUEUED,
        outreach.APPROVED,
        outreach.REJECTED,
        outreach.REJECTED_UNVERIFIABLE,
    ):
        raise HTTPException(400, f"unknown status: {status}")
    items = outreach.queue(status)
    return {
        "status": status,
        "count": len(items),
        "items": items,
        "note": "Nothing in this system sends mail. Approving records that a human is "
        "willing to send; the send happens outside it.",
    }


@router.post("/queue/{draft_id}/approve")
def post_approve(draft_id: str, body: dict = Body(default={})) -> dict:
    """Record a human's decision to send. Does not send.

    `by` is required and unvalidated on purpose: this is an accountability record, and
    an anonymous approval of a cold email about a named person is worse than none.
    """
    return _decide(outreach.approve, draft_id, body)


@router.post("/queue/{draft_id}/reject")
def post_reject(draft_id: str, body: dict = Body(default={})) -> dict:
    return _decide(outreach.reject, draft_id, body)


def _decide(fn, draft_id: str, body: dict) -> dict:
    by = str((body or {}).get("by") or "").strip()
    if not by:
        raise HTTPException(400, "`by` is required — a disposition needs a person on it")
    try:
        return fn(draft_id, by=by, note=(body or {}).get("note"))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/suppression")
def get_suppression() -> dict:
    items = outreach.suppression_list()
    return {
        "count": len(items),
        "items": items,
        "note": "Permanent by construction — there is no removal endpoint. A company or "
        "address on this list can never be drafted for.",
    }


@router.post("/suppression")
def post_suppression(body: dict = Body(...)) -> dict:
    """Add a company or address to the suppression list.

    `source: opt_out` is the recipient's own one-touch request and needs no session — an
    opt-out that requires the person to hold an account in the system that mailed them is
    not an opt-out.
    """
    raw_company = (body or {}).get("company_id")
    cid = _resolve(str(raw_company)) if raw_company else None
    try:
        return outreach.suppress(
            company_id=cid,
            email=(body or {}).get("email"),
            reason=str((body or {}).get("reason") or "no reason given"),
            source=str((body or {}).get("source") or "manual"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/history")
def get_history(company_id: Optional[str] = None) -> dict:
    """Every draft ever generated and what became of it — including the ones rejected by
    verification, which no human saw. The record is the point."""
    items = outreach.history(_resolve(company_id) if company_id else None)
    return {"count": len(items), "items": items}
