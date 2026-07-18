"""Per-company routes: scorecard, trace, score history, memo, dissent, proof. Owner: D."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from api import memo as memo_mod
from api.routers.deps import (
    as_uuid,
    degrade,
    find_seed_event,
    fixture_key,
    founder_entity_ids,
    pick,
    resolve_as_of,
    seed,
    seed_or,
)

router = APIRouter(prefix="/companies", tags=["companies"])

# THE DISSENT LOCK.
#
# Server-side, because a query flag the frontend sets is not a lock — it is a
# suggestion, and it is bypassable live on stage with a URL edit. A company lands in
# this set only when GET /companies/{id}/dissent actually served the anti-memo. The
# dissent_viewed request flag alone can NEVER unlock the recommendation.
_DISSENT_SERVED: set[str] = set()


def dissent_was_served(company_id: str) -> bool:
    return company_id in _DISSENT_SERVED


def reset_dissent_locks() -> None:
    """Test/demo-reset hook. Not routed — there is deliberately no HTTP way to unlock."""
    _DISSENT_SERVED.clear()


@router.get("/{company_id}")
def get_company(company_id: str, as_of: datetime | None = None) -> dict:
    """Detail view. Accepts either a store UUID or a fixture slug — the ranked list
    hands out UUIDs, so resolving them is what makes the list clickable."""
    key = fixture_key(company_id)
    detail = seed_or(f"company_{key}", None)
    if detail is None:
        # Only the five stage archetypes have hand-authored detail fixtures. Build
        # the rest from the event log so every row in the ranked list is clickable —
        # a demo that dead-ends on two thirds of its own list is worse than a plain page.
        detail = _detail_from_store(company_id, key)
        if detail is None:
            raise HTTPException(404, f"unknown company: {company_id}")

    # Overlay the live score so the detail page shows what the filter actually
    # computed rather than a number frozen into the fixture.
    cutoff = resolve_as_of(as_of)
    cid = as_uuid(company_id) or as_uuid(detail.get("company_id"))
    if cid:
        detail["company_id"] = str(cid)
        try:
            from memory import score as score_mod

            ents = founder_entity_ids(cid)
            if ents:
                fs = score_mod.founder(ents[0], cutoff)
                detail["founder_score"] = {
                    "mu": fs.mu,
                    "band": fs.band,
                    "trend": fs.trend,
                    "contributing_event_ids": [str(i) for i in fs.contributing_event_ids],
                    "as_of": cutoff.isoformat(),
                }
        except Exception:  # noqa: BLE001 - a fixture detail page still beats a 500
            pass
    return detail


def _detail_from_store(company_id: str, slug: str) -> dict | None:
    """A detail page assembled from the event log, for companies without a fixture."""
    from memory import store

    cid = as_uuid(company_id)
    if cid is None:
        return None
    row = store.get_company(cid)
    if not row:
        return None

    events = store.events(as_of=resolve_as_of(None), company_id=cid)
    return {
        "company_id": str(cid),
        "slug": slug,
        "name": row.get("name"),
        "archetype": row.get("archetype"),
        "source": "event_log",
        "event_count": len(events),
        "events": [
            {
                "event_id": str(e.event_id),
                "kind": str(e.kind),
                "source": str(e.source),
                "observed_at": e.observed_at.isoformat(),
                "quoted_span": e.evidence_span,
                "source_url": e.source_url,
                "integrity_flags": e.integrity_flags,
            }
            for e in sorted(events, key=lambda e: e.observed_at, reverse=True)[:60]
        ],
        "integrity_flags": sorted({f for e in events for f in e.integrity_flags}),
    }


@router.get("/{company_id}/trace/{event_id}")
def get_trace(company_id: str, event_id: str) -> dict:
    """Score -> contributing events -> source span -> original URL/slide id.

    Judges will click this. It bottoms out in `quoted_span` — a real span of text, a
    commit sha or a slide id — never merely a source name.
    """

    def live() -> dict:
        from memory import store

        eid, cid = as_uuid(event_id), as_uuid(company_id)
        if eid is None:
            raise HTTPException(400, "event_id is not a uuid")
        match = next(
            (e for e in store.events(as_of=resolve_as_of(None), company_id=cid) if e.event_id == eid),
            None,
        )
        if match is None:
            raise LookupError(f"event {event_id} not in the store")

        contributing = None
        for ent in founder_entity_ids(cid) if cid else []:
            from memory import score as score_mod

            fs = score_mod.founder(ent, resolve_as_of(None))
            if eid in fs.contributing_event_ids:
                contributing = {
                    "entity_id": str(ent),
                    "mu": fs.mu,
                    "band": fs.band,
                    "trend": fs.trend,
                }
                break

        return _trace_payload(
            company_id=company_id,
            event_id=event_id,
            kind=str(match.kind),
            source=str(match.source),
            source_url=match.source_url,
            observed_at=match.observed_at.isoformat(),
            quoted_span=match.evidence_span,
            confidence=match.confidence,
            integrity_flags=match.integrity_flags,
            payload=match.payload,
            contributing_to=contributing,
            degraded=False,
        )

    def fallback() -> dict:
        ev = find_seed_event(event_id)
        if ev is None:
            raise HTTPException(404, f"no trace for event {event_id}")
        return _trace_payload(
            company_id=company_id,
            event_id=event_id,
            kind=pick(ev, "kind", default="unknown"),
            source=pick(ev, "source", default="unknown"),
            source_url=pick(ev, "source_url", "url"),
            observed_at=pick(ev, "observed_at"),
            quoted_span=pick(ev, "evidence_span", "quoted_span", "span"),
            confidence=pick(ev, "confidence", default=1.0),
            integrity_flags=pick(ev, "integrity_flags", default=[]),
            payload=pick(ev, "payload", default={}),
            contributing_to=pick(ev, "contributing_to"),
            degraded=True,
        )

    return degrade(live, fallback)


def _trace_payload(*, quoted_span: str | None, **kw) -> dict:
    """The drill-down chain, rendered top to bottom by the UI."""
    chain = [
        {"step": "score", "detail": kw.get("contributing_to") or "not a scoring observation"},
        {"step": "event", "detail": f"{kw['kind']} via {kw['source']} at {kw['observed_at']}"},
        {
            "step": "source span",
            # No span means we cannot show a receipt — say so rather than showing the source name
            # and letting it read as evidence.
            "detail": quoted_span or "NO QUOTED SPAN STORED — this event is not citable evidence",
        },
        {"step": "original", "detail": kw.get("source_url") or "no url on file"},
    ]
    return {**kw, "quoted_span": quoted_span, "has_span": bool(quoted_span), "chain": chain}


@router.get("/{company_id}/score-history")
def get_score_history(company_id: str, as_of: datetime | None = None, points: int = 12) -> dict:
    """The moving score line + tightening band. Scores at successive dates — every point
    is a real filter run at that cutoff, not an interpolation of the final value."""
    cutoff = resolve_as_of(as_of)

    def live() -> dict:
        from memory import score as score_mod, store

        cid = as_uuid(company_id)
        ents = founder_entity_ids(cid) if cid else []
        if not ents:
            raise LookupError("no resolved founder entity for this company")
        entity_id = ents[0]

        events = store.events(as_of=cutoff, entity_id=entity_id)
        if not events:
            raise LookupError("no events to build a history from")

        start = min(e.observed_at for e in events)
        span = (cutoff - start) or timedelta(days=1)
        step = span / max(points - 1, 1)
        series = []
        for i in range(points):
            at = start + step * i
            fs = score_mod.founder(entity_id, at)
            series.append(
                {
                    "as_of": at.isoformat(),
                    "mu": fs.mu,
                    "band": fs.band,
                    "trend": fs.trend,
                    "event_count": len([e for e in events if e.observed_at <= at]),
                }
            )
        return {
            "company_id": company_id,
            "entity_id": str(entity_id),
            "series": series,
            "degraded": False,
        }

    def fallback() -> dict:
        fixture = seed_or(f"score_history_{company_id}", None)
        if fixture:
            return {**fixture, "degraded": True}
        company = seed_or(f"company_{company_id}", {})
        return {
            "company_id": company_id,
            "entity_id": pick(company, "entity_id"),
            "series": pick(company, "score_history", "series", default=[]),
            "degraded": True,
        }

    return degrade(live, fallback)


@router.get("/{company_id}/memo")
def get_memo(
    company_id: str, as_of: datetime | None = None, dissent_viewed: bool = False
) -> dict:
    """Recommendation stays null until the dissent has actually been served.

    Enforced here against server state. `dissent_viewed=true` on its own does nothing —
    that flag is a UI hint, and trusting it would make the lock decorative.
    """
    cutoff = resolve_as_of(as_of)
    result = degrade(
        lambda: memo_mod.generate_memo(company_id, cutoff),
        lambda: seed(f"memo_{company_id}"),
    )

    # Both must hold: the server must have served the anti-memo, AND the client must
    # say it rendered it. The client half alone is not sufficient — that is the lock.
    if not (dissent_was_served(company_id) and dissent_viewed):
        result["recommendation"] = None
        result["recommendation_locked_reason"] = "open the dissent view first"
    return result


@router.get("/{company_id}/dissent")
def get_dissent(company_id: str, as_of: datetime | None = None) -> dict:
    """Serving the anti-memo is the ONLY thing that unlocks the recommendation."""
    cutoff = resolve_as_of(as_of)

    def live() -> dict:
        from intelligence import dissent

        cid = as_uuid(company_id)
        anti = dissent.generate(cid, cutoff)
        out = {
            "company_id": company_id,
            "bear_case": anti.bear_case,
            "weakest_evidence": anti.weakest_evidence,
            "load_bearing_claim": anti.load_bearing_claim,
            "axis_spreads": anti.axis_spreads,
            "degraded": False,
        }
        try:
            out["uncertainty"] = dissent.uncertainty_from_spread(anti)
        except (NotImplementedError, AttributeError):
            pass
        return out

    result = degrade(live, lambda: seed(f"dissent_{company_id}"))
    _DISSENT_SERVED.add(company_id)  # only reached if the anti-memo actually rendered
    return result


class ProofSubmission(BaseModel):
    artifact: str = ""
    trace: dict = {}
    demo: bool = False  # the seeded stage path


@router.post("/{company_id}/proof")
def issue_proof(company_id: str) -> dict:
    """Issue a challenge: one ambiguous requirement, one planted bad constraint."""

    def live() -> dict:
        from intelligence import proof

        ch = proof.generate(as_uuid(company_id))
        return {
            "challenge_id": str(ch.challenge_id),
            "company_id": company_id,
            "prompt": ch.prompt,
            "central_claim": ch.central_claim,
            "ambiguous_requirement": ch.ambiguous_requirement,
            "planted_bad_constraint": ch.planted_bad_constraint,
            "issued_at": ch.issued_at.isoformat(),
            "degraded": False,
        }

    def fallback() -> dict:
        fixture = seed_or(f"challenge_{company_id}", None) or seed_or("challenge", None)
        if fixture is None:
            raise HTTPException(503, "proof protocol unavailable and no challenge fixture seeded")
        return {**fixture, "company_id": company_id, "degraded": True}

    return degrade(live, fallback)


@router.post("/{company_id}/proof/{challenge_id}/grade")
def grade_proof(
    company_id: str, challenge_id: str, submission: ProofSubmission = Body(default=None)
) -> dict:
    """Grade a submission. Graded events are appended, so the score visibly moves —
    that re-entry into the gate is the demo."""
    sub = submission or ProofSubmission()

    def live() -> dict:
        from intelligence import proof
        from memory import store

        if sub.demo:
            events = proof.seed_demo_completion(as_uuid(company_id))
        else:
            events = proof.grade(as_uuid(challenge_id), sub.artifact, sub.trace)
        appended = []
        for ev in events or []:
            store.append(ev)
            appended.append(str(ev.event_id))
        return {
            "company_id": company_id,
            "challenge_id": challenge_id,
            "graded_event_ids": appended,
            "degraded": False,
        }

    def fallback() -> dict:
        fixture = seed_or(f"proof_result_{company_id}", None) or seed_or("proof_result", None)
        if fixture is None:
            raise HTTPException(503, "grading unavailable and no proof fixture seeded")
        return {**fixture, "company_id": company_id, "challenge_id": challenge_id, "degraded": True}

    return degrade(live, fallback)
