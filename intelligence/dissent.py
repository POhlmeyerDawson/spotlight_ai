"""Dissent Engine. Owner: C. See C.md H12-16.

Same evidence graph, inverted objective. Prompt it ADVERSARIALLY — a polite balanced
take makes the whole feature read as theater. It must name the single load-bearing
claim that kills the thesis if false.

The recommendation stays null until dissent is opened, enforced in the API response
shape rather than the frontend, so it cannot be bypassed live on stage.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from uuid import UUID

from core import llm
from memory import store
from schema.events import AntiMemo

AXES = ("founder", "market", "idea_vs_market")

# A wide bull/bear gap is not a tie to be split — it means the evidence does not decide,
# and undecided pushes toward NO_CALL. The gate and D both read this number.
SPREAD_MAX_WEIGHT = 0.65  # the single worst axis dominates
SPREAD_MEAN_WEIGHT = 0.35
UNKNOWN_UNCERTAINTY = 0.5  # no spreads computed = unknown, never "certain"

UNKNOWN = "unknown"
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

SYSTEM = (
    "You are the dissenting partner in an investment committee. Your ONLY job is to kill "
    "this deal. You do not write balanced takes, you do not list strengths, and you do "
    "not soften a conclusion to sound fair — someone else already argued the bull case "
    "and your value is entirely in being the one who says no. "
    "Attack the evidence, not the person: never reference where anyone studied, who they "
    "have worked for, or who funded them, and never treat those as reasons for or "
    "against. Cite the event ids you were given, verbatim. If your argument depends on "
    "something that is not in the evidence below, you must write 'unknown' and say the "
    "evidence is missing. Inventing evidence is the one failure that disqualifies your "
    "whole memo. Respond with JSON only."
)

DISSENT_PROMPT = """Below is the complete evidence graph for one company, as of a fixed
date. Each line is one event, prefixed with its event id.

Argue for REJECTION. Be specific and be harsh.

Then answer the question that actually matters: of everything being claimed here, which
SINGLE claim, if it turned out to be false, kills the entire thesis? Name it flatly. Do
not hedge it, do not name two, do not qualify it into meaninglessness.

Finally, score each of the three axes twice: once as the most generous reading of this
evidence would (bull), once as the most sceptical reading would (bear). Where the
evidence is thin, those two numbers should be far apart — that gap is the point, so do
not compress it to look decisive.

Return JSON:
{{
  "bear_case": "the argument for passing. Several paragraphs. Cite event ids inline.",
  "weakest_evidence": ["each entry: the specific weak or missing piece of evidence, with
                       the event id it comes from, or the word 'unknown' if the weakness
                       is that nothing exists"],
  "load_bearing_claim": "the one claim that kills the thesis if false",
  "axes": {{
    "founder": {{"bull": 0.0-1.0, "bear": 0.0-1.0}},
    "market": {{"bull": 0.0-1.0, "bear": 0.0-1.0}},
    "idea_vs_market": {{"bull": 0.0-1.0, "bear": 0.0-1.0}}
  }}
}}

Known event ids (you may cite no others):
{event_ids}"""


def generate(company_id: UUID, as_of: datetime) -> AntiMemo:
    events = store.events(as_of=as_of, company_id=company_id)
    known_ids = {str(ev.event_id) for ev in events}
    digest = _digest(events)

    out = llm.complete(
        DISSENT_PROMPT.format(event_ids="\n".join(sorted(known_ids)) or "(none)"),
        system=SYSTEM,
        tier="deep",
        untrusted=digest,
        json_mode=True,
    )
    out = out if isinstance(out, dict) else {}

    weakest = [
        _scrub(str(w), known_ids) for w in (out.get("weakest_evidence") or []) if str(w).strip()
    ]
    return AntiMemo(
        company_id=company_id,
        bear_case=_scrub(_text(out, "bear_case"), known_ids) or _no_evidence_case(len(events)),
        weakest_evidence=weakest or [f"{UNKNOWN}: no weakness could be evidenced from the graph"],
        # Required output, never empty and never hedged. If the model refuses to name one,
        # the honest answer is that nothing here is yet load-bearing enough to test.
        load_bearing_claim=_scrub(_text(out, "load_bearing_claim"), known_ids)
        or f"{UNKNOWN}: no claim in the evidence graph is specific enough to be falsified",
        axis_spreads=_axis_spreads(out.get("axes")),
    )


def uncertainty_from_spread(anti_memo: AntiMemo) -> float:
    """Bull/bear disagreement -> uncertainty in [0, 1]. Rises monotonically with spread.

    The gate consumes this: a wide spread must push toward NO_CALL rather than being
    averaged away into a confident-looking middle.
    """
    spreads = [v for v in anti_memo.axis_spreads.values() if isinstance(v, (int, float))]
    if not spreads:
        return UNKNOWN_UNCERTAINTY
    worst = max(spreads)
    mean = sum(spreads) / len(spreads)
    return round(min(1.0, SPREAD_MAX_WEIGHT * worst + SPREAD_MEAN_WEIGHT * mean), 4)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _digest(events: list) -> str:
    """One line per event. Contains founder-supplied deck text, so it travels as
    untrusted= and never gets concatenated into the prompt."""
    if not events:
        return "(the evidence graph is empty)"
    lines = []
    for ev in events:
        span = (ev.evidence_span or "").strip().replace("\n", " ")
        payload = json.dumps(ev.payload, default=str)[:400]
        lines.append(
            f"[{ev.event_id}] {ev.kind} via {ev.source} at {ev.observed_at.isoformat()} "
            f"conf={ev.confidence} | {span[:300]} | {payload}"
        )
    return "\n".join(lines)


def _scrub(text: str, known_ids: set[str]) -> str:
    """Never let the anti-memo cite an event that does not exist. A hallucinated id is
    replaced with 'unknown' rather than quietly passed through to the partner reading it."""
    return _UUID_RE.sub(lambda m: m.group(0) if m.group(0).lower() in known_ids else UNKNOWN, text)


def _axis_spreads(axes: object) -> dict[str, float]:
    if not isinstance(axes, dict):
        return {}
    out = {}
    for axis in AXES:
        pair = axes.get(axis)
        if not isinstance(pair, dict):
            continue
        bull, bear = _num(pair.get("bull")), _num(pair.get("bear"))
        if bull is None or bear is None:
            continue
        out[axis] = round(abs(bull - bear), 4)
    return out


def _no_evidence_case(n_events: int) -> str:
    return (
        f"The evidence graph holds {n_events} event(s) and the dissent pass returned "
        f"nothing usable. Treat that as a reason to pass, not as an absence of risk: we "
        f"have no evidenced bear case because we have no evidence."
    )


def _text(d: dict, key: str) -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else ""


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0.0, min(1.0, float(v)))
