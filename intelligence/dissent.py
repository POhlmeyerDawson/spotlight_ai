"""Dissent Engine. Owner: C. See C.md H12-16.

Same evidence graph, inverted objective. Prompt it ADVERSARIALLY — a polite balanced
take makes the whole feature read as theater. It must name the single load-bearing
claim that kills the thesis if false.

The recommendation stays null until dissent is opened, enforced in the API response
shape rather than the frontend, so it cannot be bypassed live on stage.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from core import llm
from intelligence import flags
from schema.events import AntiMemo, Event, EventKind, ScreeningResult

Judge = Callable[..., str | dict]

SPREAD_MAX_WEIGHT = 0.65
SPREAD_MEAN_WEIGHT = 0.35
UNKNOWN_UNCERTAINTY = 0.5
# The three axes a complete spread covers. `axis_spreads` may hold FEWER than these when
# the screen could not measure one — a missing key is an undefined spread, and every
# consumer must treat it as unknown rather than as zero.
AXIS_NAMES = ("founder", "market", "idea_vs_market")

_SYSTEM = (
    "Write the strongest evidence-grounded case against proceeding. Name one specific "
    "load-bearing claim; do not write a balanced summary and do not invent evidence."
)
_PROMPT = (
    "Return JSON with bear_case (nonempty string), weakest_evidence (nonempty list of strings), "
    "load_bearing_claim (nonempty string), and bear_axes (founder, market, idea_vs_market; "
    "each 0..1), plus evidence_event_ids (only supplied ids). The axes stay separate and are "
    "never averaged.\n"
    "Score each bear axis YOURSELF from the supplied evidence, as the lowest defensible "
    "reading a sceptic could justify. You have deliberately not been shown the optimistic "
    "scores; do not guess at them or try to agree with them. An independent number is the "
    "entire point — a bear axis that matches the bull is worth nothing."
)


def _bull_axes(screening: ScreeningResult) -> dict[str, float]:
    """The bull scores, with any axis the screen could not measure OMITTED.

    A spread is `abs(bull - bear)`. Against an unmeasured bull there is no such
    quantity — it is undefined, not zero. Writing 0.0 would be the exact failure this
    codebase already shipped once: `axis_spreads` identically {0.0, 0.0, 0.0}, which
    made `uncertainty_from_spread` read as certainty, stopped "wide spread -> no-call"
    from ever firing, and rendered as three empty bars a viewer reads as "bull and bear
    agree perfectly" — the opposite of "we never scored this".

    Omitting the key is what makes the absence propagate honestly: `uncertainty_from_
    spread` already returns UNKNOWN_UNCERTAINTY when any of the three is missing, and
    the client already drops null spreads rather than plotting them (app/lib/adapt.ts).
    """
    axes = {
        "founder": screening.founder.score,
        "market": screening.market.score,
        "idea_vs_market": screening.idea_vs_market.score,
    }
    return {name: score for name, score in axes.items() if score is not None}


def _fallback(company_id: UUID, screening: ScreeningResult) -> AntiMemo:
    bear = 0.5
    return AntiMemo(
        company_id=company_id,
        bear_case="The available evidence is too thin to rule out a materially weaker case.",
        weakest_evidence=["Independent evidence is incomplete or unavailable."],
        load_bearing_claim="The central product claim works under representative conditions.",
        axis_spreads={
            name: abs(score - bear) for name, score in _bull_axes(screening).items()
        },
    )


def _event_text(event: Event) -> str:
    values = [event.evidence_span or ""]
    for key in ("claim", "title", "text", "body", "description"):
        value = event.payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return " ".join(value for value in values if value)[:500]


def generate_from_evidence(
    company_id: UUID,
    as_of: datetime,
    events: list[Event],
    screening: ScreeningResult,
    judge: Judge = llm.complete,
) -> AntiMemo:
    """Pure dissent core over one frozen, integrity-clean evidence packet.

    The filter must stay identical to the memo's (api/memo.py::_evidence): the
    anti-memo only means something if it argues from the SAME evidence graph with
    the objective inverted. A blanket integrity filter here dropped provenance-flagged
    events the memo kept, so the bear case was blind to ~20-25% of the evidence for
    exactly the Type 6 archetype — a bull and a bear reading different books.
    """
    if screening.company_id != company_id or screening.as_of != as_of:
        raise ValueError("screening snapshot does not match the frozen evidence cutoff")
    evidence = [
        event
        for event in events
        if event.company_id == company_id
        and event.observed_at <= as_of
        and event.kind != EventKind.INTEGRITY
        and not flags.is_impeached(event)
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
    # The BULL scores. They are the comparison basis and the bear agent must never see
    # them: handing them over produced a model that echoed them back verbatim (0.27343…
    # returned to four significant figures), so abs(bull - bear) was structurally zero
    # on every company. axis_spreads read {0.0, 0.0, 0.0} everywhere, uncertainty was
    # always 0 so "wide spread -> no-call" could never fire, and the UI rendered three
    # empty bars — which a viewer reads as "bull and bear agree perfectly", the exact
    # opposite of the truth. The bear agent now scores the evidence independently and
    # the spread is computed here, against numbers it was never shown.
    # Axes the screen could not measure are omitted here rather than defaulted — see
    # `_bull_axes`. The bear still scores all three from the evidence (its reading does
    # not depend on ours); we simply have no bull number to take a difference against.
    axes = _bull_axes(screening)
    if not docs:
        return _fallback(company_id, screening)
    valid_ids = {doc["event_id"] for doc in docs}
    try:
        raw = judge(
            _PROMPT,
            system=_SYSTEM,
            tier="deep",
            untrusted=json.dumps({"events": docs}),
            json_mode=True,
        )
        data = raw if isinstance(raw, dict) else json.loads(raw)
        weakest = data["weakest_evidence"]
        bear_axes = data["bear_axes"]
        cited = data["evidence_event_ids"]
        if (
            not isinstance(data["bear_case"], str)
            or not data["bear_case"].strip()
            or not isinstance(weakest, list)
            or not weakest
            or not all(isinstance(item, str) and item.strip() for item in weakest)
            or not isinstance(data["load_bearing_claim"], str)
            or not data["load_bearing_claim"].strip()
            or not isinstance(bear_axes, dict)
            or not isinstance(cited, list)
        ):
            raise ValueError("malformed dissent")
        clean_bear = {}
        # Validated over the canonical triple, not over `axes`: the bear must still score
        # all three from the evidence regardless of which ones WE could measure, and a
        # reply missing one is malformed. Only the spread is restricted to `axes`.
        for axis in AXIS_NAMES:
            value = bear_axes[axis]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("malformed bear axis")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("malformed bear axis")
            clean_bear[axis] = max(0.0, min(1.0, value))
        receipts = list(dict.fromkeys(str(value) for value in cited if str(value) in valid_ids))
        if not receipts:
            raise ValueError("dissent has no valid receipts")
        receipt_label = ",".join(receipts)
        return AntiMemo(
            company_id=company_id,
            bear_case=f"{data['bear_case'].strip()} [evidence: {receipt_label}]",
            weakest_evidence=[f"[{receipts[0]}] {item.strip()}" for item in weakest],
            load_bearing_claim=(
                f"{data['load_bearing_claim'].strip()} [evidence: {receipt_label}]"
            ),
            axis_spreads={axis: abs(axes[axis] - clean_bear[axis]) for axis in axes},
        )
    except Exception:
        return _fallback(company_id, screening)


def generate(company_id: UUID, as_of: datetime, judge: Judge = llm.complete) -> AntiMemo:
    """Store-backed dissent wrapper; every read and score uses the same as_of."""
    from intelligence import screen
    from memory import store

    events = store.events(company_id=company_id, as_of=as_of)
    screening = screen.three_axis(company_id, as_of)
    return generate_from_evidence(company_id, as_of, events, screening, judge)


def uncertainty_from_spread(anti_memo: AntiMemo) -> float:
    """Convert separate bull/bear axis gaps into bounded decision uncertainty."""
    raw = [anti_memo.axis_spreads.get(axis) for axis in AXIS_NAMES]
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
        for value in raw
    ):
        return UNKNOWN_UNCERTAINTY
    spreads = [float(value) for value in raw]
    clipped = [max(0.0, min(1.0, value)) for value in spreads]
    return round(
        min(
            1.0,
            SPREAD_MAX_WEIGHT * max(clipped) + SPREAD_MEAN_WEIGHT * (sum(clipped) / len(clipped)),
        ),
        4,
    )
