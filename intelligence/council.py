"""Evidence-grounded decision council layered on screening and dissent.

Three independent roles argue from one frozen evidence packet. A chair resolves the
decision by explicit policy, never by averaging axes or counting votes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from core import llm
from intelligence import dissent
from schema.events import AntiMemo, Event, EventKind, ScreeningResult

Judge = Callable[..., str | dict]


class CouncilDecision(StrEnum):
    REACH_OUT = "reach_out"
    PROOF_PROTOCOL = "proof_protocol"
    NO_CALL = "no_call"


class CouncilArgument(BaseModel):
    role: str
    position: CouncilDecision
    argument: str
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _nonempty_text(self) -> CouncilArgument:
        if not self.role.strip() or not self.argument.strip():
            raise ValueError("council argument text must be nonempty")
        self.role = self.role.strip()
        self.argument = self.argument.strip()
        return self


class CouncilResult(BaseModel):
    company_id: UUID
    as_of: datetime
    decision: CouncilDecision
    arguments: list[CouncilArgument]
    disagreements: list[str]
    load_bearing_question: str
    evidence_that_would_change_decision: str
    uncertainty_widening: float = Field(ge=0.0, le=1.0)
    anti_memo: AntiMemo

    @model_validator(mode="after")
    def _exact_roles(self) -> CouncilResult:
        if {argument.role for argument in self.arguments} != {
            "scout",
            "skeptic",
            "bias_auditor",
        } or len(self.arguments) != 3:
            raise ValueError("council requires exactly one argument from each role")
        if (
            not self.load_bearing_question.strip()
            or not self.evidence_that_would_change_decision.strip()
            or not all(item.strip() for item in self.disagreements)
        ):
            raise ValueError("council result text must be nonempty")
        return self


class CouncilResponse(BaseModel):
    company_id: UUID
    as_of: datetime
    decision: CouncilDecision | None
    dissent_viewed: bool
    anti_memo: AntiMemo | None = None
    decision_locked_reason: str | None = None

    @model_validator(mode="after")
    def _lock_state(self) -> CouncilResponse:
        if self.dissent_viewed:
            if self.decision is None or self.anti_memo is None or self.decision_locked_reason:
                raise ValueError("viewed dissent requires the memo and decision")
        elif (
            self.decision is not None
            or self.anti_memo is not None
            or not isinstance(self.decision_locked_reason, str)
            or not self.decision_locked_reason.strip()
        ):
            raise ValueError("locked response requires a reason and cannot expose results")
        return self


_ROLES = {
    "scout": "Make the strongest evidence-backed case for outreach.",
    "skeptic": "Make the strongest evidence-backed case against outreach.",
    "bias_auditor": (
        "Audit unsupported receipts, missing-data penalties, integrity risks, and prohibited "
        "reputation-proxy signals. Do not infer weakness from irrelevant source absence."
    ),
}
_ROLE_PROMPT = (
    "Return JSON with position (reach_out, proof_protocol, or no_call), argument (nonempty), "
    "evidence_event_ids (only supplied ids), and confidence (0..1). Keep all three screening "
    "axes separate."
)
_CHAIR_SYSTEM = (
    "Resolve the council by evidence policy, not vote count and not an average. A disagreement "
    "identifies uncertainty; it does not disappear into a blended score."
)
_CHAIR_PROMPT = (
    "Return JSON with decision (reach_out, proof_protocol, or no_call), disagreements (list of "
    "strings), load_bearing_question (nonempty), and evidence_that_would_change_decision "
    "(nonempty)."
)


def _event_text(event: Event) -> str:
    values = [event.evidence_span or ""]
    for key in ("claim", "title", "text", "body", "description"):
        value = event.payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return " ".join(value for value in values if value)[:500]


def _packet(
    company_id: UUID, as_of: datetime, events: list[Event], screening: ScreeningResult
) -> tuple[list[dict], set[str]]:
    evidence = [
        event
        for event in events
        if event.company_id == company_id
        and event.observed_at <= as_of
        and event.kind != EventKind.INTEGRITY
        and not event.integrity_flags
    ]
    docs = [
        {
            "event_id": str(event.event_id),
            "kind": str(event.kind),
            "observed_at": event.observed_at.isoformat(),
            "text": _event_text(event),
        }
        for event in evidence
    ]
    axes = {
        "founder": screening.founder.model_dump(mode="json"),
        "market": screening.market.model_dump(mode="json"),
        "idea_vs_market": screening.idea_vs_market.model_dump(mode="json"),
    }
    return [{"events": docs, "axes": axes}], {doc["event_id"] for doc in docs}


def _role_argument(
    role: str, packet: list[dict], valid_ids: set[str], judge: Judge
) -> CouncilArgument:
    try:
        raw = judge(
            _ROLE_PROMPT,
            system=_ROLES[role],
            tier="deep",
            untrusted=json.dumps(packet),
            json_mode=True,
        )
        data = raw if isinstance(raw, dict) else json.loads(raw)
        position = CouncilDecision(data["position"])
        if not isinstance(data["argument"], str) or not data["argument"].strip():
            raise ValueError("malformed council argument")
        cited = data["evidence_event_ids"]
        confidence = data["confidence"]
        if (
            not isinstance(cited, list)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
        ):
            raise ValueError("malformed council argument")
        receipts = [UUID(str(value)) for value in cited if str(value) in valid_ids]
        if not receipts:
            raise ValueError("council argument has no valid receipts")
        return CouncilArgument(
            role=role,
            position=position,
            argument=data["argument"].strip(),
            evidence_event_ids=list(dict.fromkeys(receipts)),
            confidence=max(0.0, min(1.0, float(confidence))),
        )
    except Exception:
        return CouncilArgument(
            role=role,
            position=CouncilDecision.PROOF_PROTOCOL,
            argument="The role could not reach a receipt-backed conclusion from the packet.",
            evidence_event_ids=[],
            confidence=0.0,
        )


def deliberate_from_evidence(
    company_id: UUID,
    as_of: datetime,
    events: list[Event],
    screening: ScreeningResult,
    judge: Judge = llm.complete,
) -> CouncilResult:
    """Run independent arguments and a chair over one frozen evidence packet."""
    if screening.company_id != company_id or screening.as_of != as_of:
        raise ValueError("screening snapshot does not match the frozen evidence cutoff")
    packet, valid_ids = _packet(company_id, as_of, events, screening)
    anti_memo = dissent.generate_from_evidence(company_id, as_of, events, screening, judge=judge)
    arguments = [
        _role_argument(role, packet, valid_ids, judge)
        for role in ("scout", "skeptic", "bias_auditor")
    ]
    chair_data = {
        "evidence_packet": packet,
        "arguments": [argument.model_dump(mode="json") for argument in arguments],
        "anti_memo": anti_memo.model_dump(mode="json"),
    }
    try:
        raw = judge(
            _CHAIR_PROMPT,
            system=_CHAIR_SYSTEM,
            tier="deep",
            untrusted=json.dumps(chair_data),
            json_mode=True,
        )
        data = raw if isinstance(raw, dict) else json.loads(raw)
        decision = CouncilDecision(data["decision"])
        disagreements = data["disagreements"]
        question = data["load_bearing_question"]
        change_evidence = data["evidence_that_would_change_decision"]
        if (
            not isinstance(disagreements, list)
            or not all(isinstance(item, str) and item.strip() for item in disagreements)
            or not isinstance(question, str)
            or not question.strip()
            or not isinstance(change_evidence, str)
            or not change_evidence.strip()
        ):
            raise ValueError("malformed chair response")
        receipt_backed = [argument for argument in arguments if argument.evidence_event_ids]
        blocking = any(argument.position == CouncilDecision.NO_CALL for argument in receipt_backed)
        scout_supports = any(
            argument.role == "scout"
            and argument.position == CouncilDecision.REACH_OUT
            and argument.evidence_event_ids
            for argument in arguments
        )
        no_call_supported = any(
            argument.role in {"skeptic", "bias_auditor"}
            and argument.position == CouncilDecision.NO_CALL
            and argument.evidence_event_ids
            for argument in arguments
        )
        if decision == CouncilDecision.REACH_OUT and (not scout_supports or blocking):
            decision = CouncilDecision.PROOF_PROTOCOL
        elif decision == CouncilDecision.NO_CALL and not no_call_supported:
            decision = CouncilDecision.PROOF_PROTOCOL
    except Exception:
        decision = CouncilDecision.PROOF_PROTOCOL
        disagreements = ["The chair could not resolve the evidence packet reliably."]
        question = anti_memo.load_bearing_claim
        change_evidence = "A receipt-backed proof addressing the load-bearing claim."

    widening = max(anti_memo.axis_spreads.values(), default=0.0)
    if len({argument.position for argument in arguments}) > 1:
        widening = max(widening, 0.25)
    widening = max(0.0, min(1.0, widening))
    return CouncilResult(
        company_id=company_id,
        as_of=as_of,
        decision=decision,
        arguments=arguments,
        disagreements=[item.strip() for item in disagreements],
        load_bearing_question=question.strip(),
        evidence_that_would_change_decision=change_evidence.strip(),
        uncertainty_widening=widening,
        anti_memo=anti_memo,
    )


def _run(company_id: UUID, as_of: datetime, judge: Judge) -> CouncilResult:
    from intelligence import screen
    from memory import store

    events = store.events(company_id=company_id, as_of=as_of)
    screening = screen.three_axis(company_id, as_of)
    return deliberate_from_evidence(company_id, as_of, events, screening, judge)


def deliberate(company_id: UUID, as_of: datetime, judge: Judge = llm.complete) -> CouncilResponse:
    """Public entry point: no recommendation is computed until dissent is opened."""
    return CouncilResponse(
        company_id=company_id,
        as_of=as_of,
        decision=None,
        dissent_viewed=False,
        anti_memo=None,
        decision_locked_reason="open the dissent view first",
    )


def view_dissent(company_id: UUID, as_of: datetime, judge: Judge = llm.complete) -> CouncilResponse:
    """Opening dissent returns the anti-memo and unlocks the council decision atomically."""
    result = _run(company_id, as_of, judge)
    return CouncilResponse(
        company_id=result.company_id,
        as_of=result.as_of,
        decision=result.decision,
        dissent_viewed=True,
        anti_memo=result.anti_memo,
        decision_locked_reason=None,
    )
