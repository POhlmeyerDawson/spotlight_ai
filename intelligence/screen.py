"""Three axes, never averaged. Owner: C. See C.md H3-8.

Founder | Market | Idea-vs-Market. There is deliberately no combined field on
ScreeningResult and none is computed here: a great founder on a dead market is a
DIFFERENT decision from a mediocre founder on a great one, and a mean destroys
exactly that distinction. Rank lexicographically or by explicit policy, never by
averaging.

Every axis carries evidence_event_ids. No score without receipts.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from core import llm, search
from schema.events import Axis, Event, EventKind, ScreeningResult

log = logging.getLogger(__name__)

MAX_CLAIMS = 25
MAX_RESULTS = 5

# A founder score with a band this wide is a coin flip; confidence goes to zero.
BAND_FLOOR = 0.5

UNKNOWN = Axis(score=0.0, trend=0.0, confidence=0.0, evidence_event_ids=[])

_SYSTEM = (
    "You are a screening analyst. Score only what the evidence supports and say so "
    "when it supports little. Judge the substance of the work and the market — never "
    "who the founders are or where they have been."
)

_MARKET_PROMPT = """Assess the MARKET this company is operating in, from its own claims and
the web context below. You are scoring the market itself, not the company.

Return JSON: {"score": <0..1 how attractive and real this market is>,
 "trend": <-1..1 whether it is growing or shrinking>,
 "confidence": <0..1 how much the evidence below actually supports your answer>,
 "rationale": "<two sentences>"}

Score low for markets that are shrinking, already consolidated, or that the evidence
does not show exists. confidence must be low when the context is thin — that is the
honest answer and it is more useful than a confident guess."""

_FIT_PROMPT = """Assess IDEA-vs-MARKET FIT: does this specific idea address this specific
market's actual pain, at the right moment, in a way the market can adopt?

Return JSON: {"score": <0..1 fit>, "trend": <-1..1 improving or decaying>,
 "confidence": <0..1>, "rationale": "<two sentences>"}

A strong idea in the wrong market scores low here. So does a right-market idea that
nobody in that market can actually buy or adopt. Timing counts."""


def _company_events(company_id: UUID, as_of: datetime) -> list[Event]:
    try:
        from memory import store

        return store.events(as_of=as_of, company_id=company_id)
    except Exception as exc:
        log.warning("screen: events unavailable for %s (%s)", company_id, exc)
        return []


def _company_name(company_id: UUID) -> str:
    try:
        from memory import store

        return (store.get_company(company_id) or {}).get("name") or ""
    except Exception:
        return ""


def _founder_axis(events: list[Event], as_of: datetime) -> Axis:
    """Reads A's score.founder(). Never re-derived here.

    Several founders are not averaged either — we take the strongest, and carry
    that founder's receipts.
    """
    best: Axis | None = None
    for entity_id in dict.fromkeys(e.entity_id for e in events if e.entity_id):
        try:
            from memory import score

            fs = score.founder(entity_id, as_of)
        except Exception as exc:  # A's filter is built in parallel — degrade, don't crash
            log.warning("screen: founder score unavailable for %s (%s)", entity_id, exc)
            continue
        axis = Axis(
            score=min(max(fs.mu, 0.0), 1.0),
            trend=fs.trend,
            confidence=round(max(0.0, 1.0 - fs.band / BAND_FLOOR), 3),
            evidence_event_ids=list(fs.contributing_event_ids),
        )
        if best is None or axis.score > best.score:
            best = axis
    return best or UNKNOWN


def _claim_context(claims: list[Event]) -> str:
    lines = []
    for e in claims[:MAX_CLAIMS]:
        text = e.payload.get("claim") or e.payload.get("text") or e.evidence_span or ""
        if text:
            lines.append(f"- [{e.evidence_span or e.event_id}] {text}")
    return "\n".join(lines)


def _web_context(query: str) -> str:
    if not query.strip():
        return ""
    try:
        results = search.search(query, max_results=MAX_RESULTS)
    except Exception as exc:
        log.warning("screen: web context unavailable (%s)", exc)
        return ""
    return "\n\n".join(f"[{r.url}] {r.title}\n{r.snippet}" for r in results)


def _llm_axis(prompt: str, context: str, evidence_ids: list[UUID]) -> Axis:
    if not context.strip():
        return UNKNOWN  # no evidence, no score. Say nothing rather than guess.
    try:
        out = llm.complete(prompt, system=_SYSTEM, tier="fast", untrusted=context, json_mode=True)
    except Exception as exc:
        log.warning("screen: axis assessment failed (%s)", exc)
        return UNKNOWN
    if not isinstance(out, dict):
        return UNKNOWN

    def _f(key: str, lo: float, hi: float) -> float:
        v = out.get(key)
        return min(max(float(v), lo), hi) if isinstance(v, (int, float)) else 0.0

    return Axis(
        score=_f("score", 0.0, 1.0),
        trend=_f("trend", -1.0, 1.0),
        confidence=_f("confidence", 0.0, 1.0),
        evidence_event_ids=evidence_ids,
    )


def three_axis(company_id: UUID, as_of: datetime) -> ScreeningResult:
    events = _company_events(company_id, as_of)
    claims = [e for e in events if str(e.kind) == str(EventKind.DECK_CLAIM)]
    claim_ids = [e.event_id for e in claims]

    context = _claim_context(claims)
    web = _web_context(f"{_company_name(company_id)} market size growth competitors".strip())
    combined = f"COMPANY'S OWN CLAIMS:\n{context}\n\nWEB CONTEXT:\n{web}" if context or web else ""

    return ScreeningResult(
        company_id=company_id,
        as_of=as_of,
        founder=_founder_axis(events, as_of),
        market=_llm_axis(_MARKET_PROMPT, combined, claim_ids),
        idea_vs_market=_llm_axis(_FIT_PROMPT, combined, claim_ids),
    )
