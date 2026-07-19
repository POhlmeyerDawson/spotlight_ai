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
from intelligence import flags
from schema.events import Axis, Event, EventKind, FounderScore, ScreeningResult

Judge = Callable[..., str | dict]

_SNIPPET_MAX = 400

_MARKET_SYSTEM = (
    "You score the MARKET axis for ONE company. The evidence events you are shown "
    "belong to that company alone.\n\n"
    "CRITICAL — do not score the sector. Every company you are asked about is a "
    "{sector} company, so the liveness of that space is a CONSTANT across the whole "
    "corpus and cannot tell any two of them apart. An answer of the form 'this is a "
    "hot, growing space' is the same answer for every company and makes this axis "
    "useless.\n\n"
    "Score instead the market pull that THIS company's own evidence demonstrates: who "
    "is actually adopting it, how fast that adoption moves, whether anyone outside the "
    "company depends on it or pays for it, and whether the specific problem this "
    "company attacks shows real demand. Score strictly from the evidence events "
    "provided — no outside knowledge, no assumptions about the people involved."
)
# The saturation this replaces: the previous wording asked "how alive is the problem
# space right now", which is a property of the THESIS SECTOR, not of the company. The
# sector is injected identically into all 28 prompts, so the judge answered the same
# question 28 times and returned the sector's liveness — verbatim rationales of the
# form "a strong and growing interest in the AI infrastructure space" for companies
# with completely disjoint evidence. Different evidence, same question, same answer.
# The literal used to be "an AI-infra / dev-tools company", hardcoded. A fund whose
# thesis said fintech got a judge told it was reading dev-tools, and the judge duly
# scored a payments company's timing against a market it was not in. The sector now
# comes from the active thesis, and falls back to a neutral phrasing rather than to
# somebody else's industry when the thesis names none.
_MARKET_SECTOR_FALLBACK = "early-stage technology"


def _thesis_sector_phrase() -> str:
    """Human-readable sector for the judge, from `data/seed/thesis.json`."""
    from core import thesis as thesis_mod

    try:
        t = thesis_mod.load()
        labels = [
            str(s.get("label") or s.get("id")).strip()
            for s in (t.get("sectors") or [])
            if isinstance(s, dict) and s.get("include", True) and (s.get("label") or s.get("id"))
        ]
    except Exception:  # noqa: BLE001 — an unreadable thesis must not fail the axis
        labels = []
    return " / ".join(labels[:3]) if labels else _MARKET_SECTOR_FALLBACK


def _market_system() -> str:
    return _MARKET_SYSTEM.format(sector=_thesis_sector_phrase())

_IDEA_VS_MARKET_SYSTEM = (
    "You score the IDEA-VS-MARKET axis for ONE company: does THIS specific approach fit "
    "where the market actually is — wedge, differentiation, why-now.\n\n"
    "CRITICAL — do not score the category. That this is a promising area, or that "
    "developers want better tooling, is true of every company in this corpus and so "
    "distinguishes none of them. Score what is specific to this company's approach: "
    "what wedge the evidence shows it chose, whether that wedge is differentiated from "
    "the obvious alternative, and whether the timing argument is supported by the "
    "evidence rather than merely asserted by the company. Score strictly from the "
    "evidence events provided — no outside knowledge."
)
_AXIS_PROMPT = (
    "Evidence events follow as JSON (id, kind, observed_at, text). Return JSON with "
    'keys: "score" (0..1), "trend" (-1..1), "confidence" (0..1, how well the evidence '
    'supports the score), "evidence_event_ids" (ids you relied on — ONLY ids from the '
    'provided list), "rationale" (one paragraph).\n\n'
    "CALIBRATION — use the full range, and give TWO DECIMALS:\n"
    "  0.00-0.20  the evidence actively argues against this axis\n"
    "  0.20-0.40  little support, or only the company's own claims about itself\n"
    "  0.40-0.60  genuinely mixed or ambiguous evidence\n"
    "  0.60-0.80  clear support corroborated outside the company\n"
    "  0.80-1.00  exceptional and multiply corroborated — rare, most companies are not here\n"
    "Do not round to the nearest 0.1. 0.62 and 0.71 are different answers and that "
    "difference is the entire point of the axis.\n\n"
    "trend is a DIRECTION, not a rate: the SIGN says whether this axis is improving (+) "
    "or deteriorating (-) across the observed_at window, and the magnitude within 0..1 "
    "says how pronounced that direction is. Use 0.0 when the evidence is flat or too "
    "short to establish a direction. Do not default to +1 — reserve it for a direction "
    "that is unambiguous across the whole window.\n\n"
    "Thin or missing evidence means LOW CONFIDENCE. It does not mean a low score and it "
    "does not mean a middling score — report what the evidence you have actually "
    "supports, and let confidence carry how little of it there is."
)

# Unscorable axis: judge failed or nothing to judge. Never a crash, never fabricated —
# and, since the 0.5 that used to sit here, never a NUMBER either. A middling score is
# not the honest answer to "we could not measure this"; it is a confident claim made on
# no evidence, and it fed `rank_key`, letting an unmeasured axis outrank a genuinely
# weak one. None renders as absence, which is the true answer. Each call site passes
# its own reason so the client can say WHICH failure this was.
#
# `confidence` is None here for the SAME reason `score` is. It used to be 0.0, which
# undid the rest of this function one line later: `custom_council` folds axis
# confidences into an evidence-sufficiency mean, so a fabricated 0.0 said "measured,
# and worthless" where the truth was "never measured". Absence has to propagate all the
# way, not stop at the score field.
def _unscorable(reason: str) -> "Axis":
    return Axis(score=None, trend=None, confidence=None, reason=reason)


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
        event
        for event in events
        if event.kind != EventKind.INTEGRITY and not flags.is_impeached(event)
    ]
    if not events:
        return _unscorable("no events on this company at this as_of — nothing to judge")

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
            return _unscorable(
                "the judge cited no event we hold, so the reading has no receipts"
            )
        return Axis(
            score=_clip(values[0], 0.0, 1.0),
            trend=_clip(values[1], -1.0, 1.0),
            confidence=_clip(values[2], 0.0, 1.0),
            evidence_event_ids=receipts,
        )
    except Exception:
        return _unscorable("the judge failed or returned a reply we could not parse")


def market_axis(events: list[Event], judge: Judge = llm.complete) -> Axis:
    return _llm_axis(events, judge, _market_system())


def idea_vs_market_axis(events: list[Event], judge: Judge = llm.complete) -> Axis:
    return _llm_axis(events, judge, _IDEA_VS_MARKET_SYSTEM)


def three_axis(company_id: UUID, as_of: datetime) -> ScreeningResult:
    """Store-backed entry point (SHARED §4). Every read below is as_of-scoped."""
    from intelligence import team as team_mod
    from memory import store

    events = store.events(company_id=company_id, as_of=as_of)
    # The Founder axis used to read `entity_ids[0]` — the first entity in event order,
    # which is not even a deterministic choice of founder, let alone the right one. A
    # two-founder company where the second carries the technical signal scored as if
    # that person did not exist. It now aggregates every resolved founder; for the solo
    # companies that make up the current corpus the result is byte-identical to the old
    # single-entity read, because a team of one aggregates to itself.
    if any(e.entity_id is not None for e in events):
        founder = team_mod.team_axis(team_mod.team_score(company_id, as_of, events))
    else:
        founder = _unscorable("no event on this company resolves to a founder entity")

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
