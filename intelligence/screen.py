"""Three axes, never averaged. Owner: C. See C.md H3-8.

Founder | Market | Idea-vs-Market. A great founder on a dead market is a different
decision than a mediocre founder on a great one — averaging destroys exactly that
distinction, so no mean exists here or anywhere downstream. Ranking uses an explicit
lexicographic policy (`rank_key`), never a blend.

The founder axis READS A's filter output; it never re-derives it. The other two axes
are LLM-judged from as_of-scoped company events, with receipts: the judge may only
cite event ids it was shown, and anything it invents is dropped.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from numbers import Real
from uuid import UUID

from core import llm
from schema.events import Axis, Event, EventKind, FounderScore, ScreeningResult

Judge = Callable[..., str | dict]

_SNIPPET_MAX = 400

_MARKET_SYSTEM = (
    "You score the MARKET axis for an AI-infra / dev-tools company: timing, pull, and "
    "how alive the problem space is right now. Score strictly from the evidence events "
    "provided — no outside knowledge, no assumptions about the people involved."
)
_IDEA_VS_MARKET_SYSTEM = (
    "You score the IDEA-VS-MARKET axis: does this specific approach fit where the "
    "market actually is — wedge, differentiation, why-now. Score strictly from the "
    "evidence events provided — no outside knowledge."
)
_AXIS_PROMPT = (
    "Evidence events follow as JSON (id, kind, observed_at, text). Return JSON with "
    'keys: "score" (0..1), "trend" (-1..1, direction over time), "confidence" (0..1, '
    'how well the evidence supports the score), "evidence_event_ids" (ids you relied '
    'on — ONLY ids from the provided list), "rationale" (one paragraph). '
    "Thin or missing evidence means low confidence, never an invented score."
)

# Uninformative axis: judge failed or nothing to judge. Never a crash, never fabricated.
_FALLBACK = {"score": 0.5, "trend": 0.0, "confidence": 0.0}


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _snippet(e: Event) -> str:
    parts = [e.evidence_span or ""]
    for key in ("title", "text", "body", "claim", "abstract", "description"):
        v = e.payload.get(key)
        if isinstance(v, str):
            parts.append(v)
    return " ".join(p for p in parts if p)[:_SNIPPET_MAX]


def founder_axis(fs: FounderScore) -> Axis:
    """A's filter output, reshaped. score=mu, trend=nu, confidence narrows with the band."""
    return Axis(
        score=fs.mu,
        trend=fs.trend,
        confidence=_clip(1.0 - fs.band, 0.0, 1.0),
        evidence_event_ids=list(fs.contributing_event_ids),
    )


def _llm_axis(events: list[Event], judge: Judge, system: str) -> Axis:
    events = [
        event for event in events if event.kind != EventKind.INTEGRITY and not event.integrity_flags
    ]
    if not events:
        return Axis(**_FALLBACK)

    docs = [
        {
            "event_id": str(e.event_id),
            "kind": str(e.kind),
            "observed_at": e.observed_at.isoformat(),
            "text": _snippet(e),
        }
        for e in events
    ]
    valid_ids = {d["event_id"] for d in docs if d["text"].strip()}

    try:
        raw = judge(
            _AXIS_PROMPT,
            system=system,
            tier="fast",
            untrusted=json.dumps(docs),  # event text is founder/web-supplied: Invariant #4
            json_mode=True,
        )
        data = raw if isinstance(raw, dict) else json.loads(raw)
        required = {"score", "trend", "confidence", "evidence_event_ids", "rationale"}
        if not isinstance(data, dict) or not required.issubset(data):
            raise ValueError("malformed axis response")
        if (
            not isinstance(data["evidence_event_ids"], list)
            or not isinstance(data["rationale"], str)
            or not data["rationale"].strip()
        ):
            raise ValueError("malformed axis response")
        raw_values = [data[key] for key in ("score", "trend", "confidence")]
        if not all(isinstance(value, Real) and not isinstance(value, bool) for value in raw_values):
            raise ValueError("malformed axis value")
        values = [float(value) for value in raw_values]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non-finite axis value")
        cited = list(dict.fromkeys(str(i) for i in data["evidence_event_ids"]))
        receipts = [UUID(i) for i in cited if i in valid_ids]  # no invented receipts
        if not receipts:
            return Axis(**_FALLBACK)
        return Axis(
            score=_clip(values[0], 0.0, 1.0),
            trend=_clip(values[1], -1.0, 1.0),
            confidence=_clip(values[2], 0.0, 1.0),
            evidence_event_ids=receipts,
        )
    except Exception:
        return Axis(**_FALLBACK)


def market_axis(events: list[Event], judge: Judge = llm.complete) -> Axis:
    return _llm_axis(events, judge, _MARKET_SYSTEM)


def idea_vs_market_axis(events: list[Event], judge: Judge = llm.complete) -> Axis:
    return _llm_axis(events, judge, _IDEA_VS_MARKET_SYSTEM)


def three_axis(company_id: UUID, as_of: datetime) -> ScreeningResult:
    """Store-backed entry point (SHARED §4). Every read below is as_of-scoped."""
    from memory import score as founder_filter
    from memory import store

    events = store.events(company_id=company_id, as_of=as_of)
    entity_ids = [e.entity_id for e in events if e.entity_id is not None]
    if entity_ids:
        founder = founder_axis(founder_filter.founder(entity_ids[0], as_of))
    else:
        founder = Axis(**_FALLBACK)

    return ScreeningResult(
        company_id=company_id,
        as_of=as_of,
        founder=founder,
        market=market_axis(events),
        idea_vs_market=idea_vs_market_axis(events),
    )


def rank_key(sr: ScreeningResult) -> tuple[float, float, float]:
    """Explicit ranking POLICY for D's list: founder first, then fit, then market.

    Lexicographic by design — this is a stated preference ordering, not a blended
    score. Changing the policy means changing this tuple, in the open.
    """
    return (sr.founder.score, sr.idea_vs_market.score, sr.market.score)
