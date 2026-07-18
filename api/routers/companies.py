"""Per-company routes: scorecard, trace, score history, memo, dissent, proof. Owner: D."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from api import attest
from api import memo as memo_mod
from api.routers.deps import (
    as_uuid,
    degrade,
    find_seed_event,
    company_uuid,
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

    cutoff = resolve_as_of(as_of)
    detail = _normalize_detail(detail, company_id, cutoff)

    # Overlay the live score so the detail page shows what the filter actually
    # computed rather than a number frozen into the fixture.
    cid = company_uuid(company_id) or as_uuid(detail.get("company_id"))
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


def _normalize_detail(detail: dict, company_id: str, cutoff) -> dict:
    """Emit the client's CompanyDetail contract instead of the fixture's own shape.

    The detail payload disagreed with the list on three things at once: axes came back
    0..1 where the list sends 0..100, `gate` was an object where the list sends a
    string, and `events[]` was absent entirely. The dashboard papered over all of it in
    app/lib/adapt.ts. An adapter that translates between two of our own endpoints is a
    bug with a shim on top, so the translation belongs here, once.
    """
    from api.main import _ranked_row

    out = dict(detail)

    # Same builder as the ranked list, so the two endpoints cannot drift again.
    # Resolved to a UUID first: _ranked_row looks the founder up by id, and handing it
    # a slug silently produced no live axis, so the fixture's authored score won and
    # the detail page disagreed with the list it was reached from.
    cid = company_uuid(company_id)
    row = {
        "company_id": str(cid) if cid else out.get("company_id"),
        "name": out.get("name"),
        "archetype": out.get("archetype"),
    }
    try:
        summary = _ranked_row(row, cutoff)
        out |= {k: summary[k] for k in summary if k not in ("axes",)}
        merged = dict(summary.get("axes") or {})
        for name, axis in (out.get("axes") or {}).items():
            if name not in merged and isinstance(axis, dict):
                merged[name] = _axis_to_client(axis)
        out["axes"] = merged
    except Exception:  # noqa: BLE001 - a detail page still beats a 500
        out["axes"] = {
            k: _axis_to_client(v) for k, v in (out.get("axes") or {}).items() if isinstance(v, dict)
        }

    gate = detail.get("gate")
    out["gate"] = gate.get("outcome") if isinstance(gate, dict) else gate
    out["gate_rationale"] = gate.get("rationale") if isinstance(gate, dict) else None

    out.setdefault("events", _events_for(company_id, cutoff))
    out.setdefault("claims", [])
    out.setdefault("integrity", _integrity_for(company_id, cutoff))
    out.setdefault("proof_protocol", None)
    out.setdefault("entity_resolution_note", None)
    return out


def _axis_to_client(axis: dict) -> dict:
    """Fixture axes are authored 0..1; the client's Axis is 0..100 in score units."""
    scale = (lambda v: None if v is None else round(float(v) * 100, 1)) if (
        isinstance(axis.get("score"), (int, float)) and axis["score"] <= 1.0
    ) else (lambda v: v)
    return {
        **axis,
        "score": scale(axis.get("score")),
        "band": scale(axis.get("band")),
        "trend": scale(axis.get("trend")),
        "evidence_event_ids": axis.get("evidence_event_ids")
        or [str(e.get("event_ref") or "") for e in (axis.get("evidence") or [])],
    }


def _events_for(company_id: str, cutoff) -> list[dict]:
    from memory import store

    cid = company_uuid(company_id)
    if cid is None:
        return []
    return [
        {
            "event_id": str(e.event_id),
            "kind": str(e.kind),
            "source": str(e.source),
            "observed_at": e.observed_at.isoformat(),
            "evidence_span": e.evidence_span,
            "quoted_span": e.evidence_span,
            "source_url": e.source_url,
            "confidence": e.confidence,
            "integrity_flags": e.integrity_flags,
        }
        for e in sorted(
            store.events(as_of=cutoff, company_id=cid), key=lambda e: e.observed_at, reverse=True
        )[:80]
    ]


def _integrity_for(company_id: str, cutoff) -> list[dict]:
    """Surfaced, never silent — a provenance note the founder should not be punished for."""
    return [
        {"flag": f, "event_id": e["event_id"], "evidence_span": e.get("quoted_span")}
        for e in _events_for(company_id, cutoff)
        for f in (e.get("integrity_flags") or [])
    ]


def _detail_from_store(company_id: str, slug: str) -> dict | None:
    """A detail page assembled from the event log, for companies without a fixture."""
    from memory import store

    cid = company_uuid(company_id)
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

        eid, cid = as_uuid(event_id), company_uuid(company_id)
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

        cid = company_uuid(company_id)
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

        cid = company_uuid(company_id)

        # Prefer C's combined dissent/council view when it exists — it is the
        # richer artifact. Falls back to the plain anti-memo, so this route keeps
        # working whether or not the council has shipped.
        combined = _council_view(cid, cutoff)
        if combined is not None:
            return {"company_id": company_id, **combined, "degraded": False}

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


def _council_view(cid, cutoff) -> dict | None:
    """intelligence.council.view_dissent, when C has shipped it."""
    try:
        from intelligence import council
    except ImportError:
        return None
    try:
        view = council.view_dissent(cid, cutoff)
    except (AttributeError, NotImplementedError):
        return None
    return view if isinstance(view, dict) else view.model_dump(mode="json")


@router.post("/{company_id}/council")
def run_council(company_id: str, as_of: datetime | None = None) -> dict:
    """Run C's AI Council. Like the dissent view, actually serving a council
    deliberation is what unlocks the recommendation — never a client boolean."""
    cutoff = resolve_as_of(as_of)

    def live() -> dict:
        from intelligence import council

        out = council.deliberate(company_uuid(company_id), cutoff)
        out = out if isinstance(out, dict) else out.model_dump(mode="json")
        return {"company_id": company_id, **out, "degraded": False}

    def fallback() -> dict:
        fixture = seed_or(f"council_{company_id}", None)
        if fixture is None:
            raise HTTPException(503, "the AI Council is not available yet")
        return {**fixture, "company_id": company_id, "degraded": True}

    result = degrade(live, fallback)
    _DISSENT_SERVED.add(company_id)
    return result


class ProofSubmission(BaseModel):
    artifact: str = ""
    trace: dict = {}
    demo: bool = False  # the seeded stage path
    # A public repo lets the server read the commit history itself instead of
    # taking the submitter's word for it. Optional, and the strongest attestation
    # available — see api/attest.py.
    repo_url: str | None = None


@router.post("/{company_id}/proof")
def issue_proof(company_id: str) -> dict:
    """Issue a challenge: one ambiguous requirement, one planted bad constraint."""

    def live() -> dict:
        from intelligence import proof

        ch = proof.generate(company_uuid(company_id))
        # The server's own record of when this went out. Without it a submitted
        # trace cannot be placed in time except by trusting the submitter.
        attest.record_issue(str(ch.challenge_id), ch.issued_at, str(company_uuid(company_id)))
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

        cid = company_uuid(company_id)

        # A challenge is written against ONE company's central technical claim.
        # Grading it onto another company's founder score would let a submission
        # for an easy challenge inflate an unrelated founder. Rejected before any
        # event is appended, because the log has no undo.
        if attest.challenge_belongs_to(challenge_id, cid) is False:
            raise HTTPException(
                409,
                f"challenge {challenge_id} was not issued for company {company_id}",
            )

        # Split the trace into what we observed and what we were told, BEFORE
        # grading. Pushing back on the planted constraint is worth half the
        # behavioural score, so accepting it on the client's word would hand the
        # sharpest signal in the system to anyone willing to assert it. The
        # attestation rides inside the trace so the grader can weight
        # self-reported behaviour down at scoring time, not merely afterwards.
        graded_trace, attestation = attest.attest(
            challenge_id, sub.trace, repo_url=sub.repo_url, demo=sub.demo
        )

        artifact, trace, cid_for_grade = sub.artifact, graded_trace, challenge_id
        if sub.demo:
            # seed_demo_completion returns the pre-run artifact + trace, not events.
            # Running it through the real grader is both more honest for the demo
            # and what keeps the seeded path on the same code path as a live one.
            seeded = proof.seed_demo_completion(cid)
            if isinstance(seeded, dict):
                artifact = seeded.get("artifact", artifact)
                trace = {**seeded.get("trace", {}), "attestation": attestation}
                cid_for_grade = seeded.get("challenge_id", challenge_id)
            else:
                events = attest.apply(list(seeded or []), attestation)
                for ev in events:
                    store.append(ev)
                return {
                    "company_id": company_id,
                    "challenge_id": challenge_id,
                    "graded_event_ids": [str(e.event_id) for e in events],
                    "attestation": attestation,
                    "degraded": False,
                }

        events = proof.grade(as_uuid(cid_for_grade), artifact, trace)
        events = attest.apply(events, attestation)
        appended = []
        for ev in events or []:
            store.append(ev)
            appended.append(str(ev.event_id))
        return {
            "company_id": company_id,
            "challenge_id": challenge_id,
            "graded_event_ids": appended,
            "attestation": attestation,
            "degraded": False,
        }

    def fallback() -> dict:
        fixture = seed_or(f"proof_result_{company_id}", None) or seed_or("proof_result", None)
        if fixture is None:
            # Say which of the two things went wrong. An unknown challenge is a
            # client error — we cannot grade a submission for a challenge we never
            # issued, and pretending otherwise would grade an unanchored trace.
            if not attest.issued_at(challenge_id):
                raise HTTPException(
                    422,
                    f"challenge {challenge_id} was not issued by this server, so the "
                    "submitted trace cannot be anchored in time or graded",
                )
            raise HTTPException(503, "grading unavailable and no proof fixture seeded")
        # The degraded path must still say what it is. Returning a graded-looking
        # result with no attestation block is how an unverified trace ends up read
        # as an observed one.
        _, attestation = attest.attest(
            challenge_id, sub.trace, repo_url=sub.repo_url, demo=sub.demo
        )
        return {
            **fixture,
            "company_id": company_id,
            "challenge_id": challenge_id,
            "attestation": attestation,
            "degraded": True,
        }

    return degrade(live, fallback)
