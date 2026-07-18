"""Decision gate. Owner: C. See C.md H8-12.

The absence classifier is the delicate part: signal-absent-because-irrelevant
(a designer with no GitHub) vs signal-absent-and-suspicious (an infra founder
claiming a distributed system with no code anywhere). Get this wrong and we punish
exactly the founders this thesis exists to find.

So absence is only ever suspicious relative to what the founder themselves claimed.
Missing code is a question for someone who says they built a system; it is not a
question at all for someone who never said they did.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from uuid import UUID

from intelligence import screen, validator
from intelligence.flags import CODE_KINDS
from schema.events import (
    Axis,
    ClaimStatus,
    Event,
    EventKind,
    GateDecision,
    GateOutcome,
    ScreeningResult,
)

log = logging.getLogger(__name__)

# Claims that only hold if code exists somewhere. An absence here is a real question.
BUILD_CLAIM_MARKERS = (
    "distributed system",
    "we built",
    "our engine",
    "our platform",
    "database",
    "compiler",
    "kernel",
    "inference",
    "training run",
    "api",
    "sdk",
    "runtime",
    "protocol",
    "throughput",
    "latency",
    "benchmark",
    "open source",
    "our model",
    "pipeline",
)

# Work whose artifacts legitimately live outside a code host. Absence proves nothing.
NON_CODE_CRAFT_MARKERS = (
    "design",
    "brand",
    "community",
    "content",
    "curation",
    "marketplace",
    "logistics",
    "clinic",
    "supply",
    "wholesale",
    "creator",
    "editorial",
    "retail",
)

def _present(markers: tuple[str, ...], text: str) -> list[str]:
    # Word-anchored: "api" must not fire on "capital".
    return [m for m in markers if re.search(rf"\b{re.escape(m)}\b", text)]


THIN_EVIDENCE_EVENTS = 5  # below this we create evidence rather than guess at it
LOW_CONFIDENCE = 0.35
DEAD_MARKET_SCORE = 0.25
DEAD_MARKET_CONFIDENCE = 0.5


def _company_events(company_id: UUID, as_of: datetime) -> list[Event]:
    try:
        from memory import store

        return store.events(as_of=as_of, company_id=company_id)
    except Exception as exc:
        log.warning("gate: events unavailable for %s (%s)", company_id, exc)
        return []


def _claim_text(events: list[Event]) -> str:
    return " ".join(
        f"{e.payload.get('claim', '')} {e.evidence_span or ''}"
        for e in events
        if str(e.kind) == str(EventKind.DECK_CLAIM)
    ).lower()


def classify_absence(events: list[Event]) -> tuple[bool, str]:
    """Is the missing signal irrelevant, or is it the signal the claim depends on?"""
    if any(str(e.kind) in {str(k) for k in CODE_KINDS} for e in events):
        return False, "build evidence is present, so nothing is missing"

    text = _claim_text(events)
    if not text.strip():
        return False, "no claims on record, so there is nothing an absence contradicts"

    claimed_build = _present(BUILD_CLAIM_MARKERS, text)
    if not claimed_build:
        craft = _present(NON_CODE_CRAFT_MARKERS, text)
        why = f" — this is {craft[0]} work" if craft else ""
        return False, f"no build claim was made, so absent code proves nothing{why}"

    return True, (
        f"claims depend on something built ({', '.join(claimed_build[:3])}) "
        f"but no code, release or artifact exists anywhere in the record"
    )


def _axis_line(name: str, axis: Axis) -> str:
    return (
        f"  {name}: score {axis.score:.2f}, trend {axis.trend:+.2f}, "
        f"confidence {axis.confidence:.2f} "
        f"({len(axis.evidence_event_ids)} receipt{'' if len(axis.evidence_event_ids) == 1 else 's'})"
    )


def _decide(
    screening: ScreeningResult,
    contradicted: int,
    substantive: int,
    suspicious: bool,
    proven: bool,
) -> tuple[GateOutcome, str]:
    """Contradiction reprices the CLAIM, not the deal — which is why a single
    contradiction never reaches NO_CALL on its own."""
    market = screening.market
    if market.score <= DEAD_MARKET_SCORE and market.confidence >= DEAD_MARKET_CONFIDENCE:
        return GateOutcome.NO_CALL, "the market itself does not hold up, and we are confident of that"

    if contradicted >= 2 and suspicious:
        return GateOutcome.NO_CALL, (
            f"{contradicted} claims contradicted by newer independent sources, and the "
            f"evidence that would settle them does not exist"
        )

    if suspicious and not proven:
        return GateOutcome.PROOF_PROTOCOL, "the load-bearing evidence is absent where it should exist"

    if substantive < THIN_EVIDENCE_EVENTS:
        return GateOutcome.PROOF_PROTOCOL, (
            f"only {substantive} substantive events on record — too thin to judge, so create evidence"
        )

    if screening.founder.confidence < LOW_CONFIDENCE:
        return GateOutcome.PROOF_PROTOCOL, "the founder band is too wide to act on"

    return GateOutcome.PROCEED, "evidence is sufficient to make a call"


def evaluate(company_id: UUID, as_of: datetime) -> GateDecision:
    events = _company_events(company_id, as_of)
    screening = screen.three_axis(company_id, as_of)

    try:
        verdicts = validator.check_claims(company_id, as_of)
    except Exception as exc:
        log.warning("gate: validation unavailable for %s (%s)", company_id, exc)
        verdicts = []

    contradicted = sum(1 for v in verdicts if v.status == ClaimStatus.CONTRADICTED)
    unverifiable = sum(1 for v in verdicts if v.status == ClaimStatus.UNVERIFIABLE)
    substantive = sum(
        1
        for e in events
        if str(e.kind) not in {str(EventKind.PROFILE_FACT), str(EventKind.DECK_CLAIM)}
    )
    proven = any(str(e.kind) == str(EventKind.PROOF_ARTIFACT) for e in events)

    suspicious, absence_why = classify_absence(events)
    outcome, why = _decide(screening, contradicted, substantive, suspicious, proven)

    rationale = "\n".join(
        [
            f"{outcome.value.upper()}: {why}.",
            "",
            "Axes (never averaged — each is a separate decision input):",
            _axis_line("Founder", screening.founder),
            _axis_line("Market", screening.market),
            _axis_line("Idea-vs-market", screening.idea_vs_market),
            "",
            f"Claims: {len(verdicts)} checked — {contradicted} contradicted, "
            f"{unverifiable} unverifiable. A contradiction reprices that claim, not the deal.",
            f"Evidence: {substantive} substantive events"
            + (", including a graded proof artifact" if proven else ""),
            f"Absence: {'SUSPICIOUS' if suspicious else 'not suspicious'} — {absence_why}.",
        ]
    )
    return GateDecision(
        company_id=company_id,
        outcome=outcome,
        rationale=rationale,
        absence_is_suspicious=suspicious,
    )
