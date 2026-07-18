"""Outreach draft from the evidence trace. Owner: B. Cut item #3.

Every sentence must be traceable to an event we actually observed — "we noticed your
work on X" only lands when X is real. Event payloads are third-party text, so they go
through llm.complete(untrusted=...), never concatenated into the prompt.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from core import llm
from memory import store
from schema.events import EventKind, utcnow

EVIDENCE_KINDS = (
    EventKind.REPO_ACTIVITY,
    EventKind.RELEASE,
    EventKind.PAPER,
    EventKind.HN_POST,
    EventKind.HN_COMMENT,
)
MAX_EVIDENCE = 8

SYSTEM = (
    "You write short, specific outreach from an investor to a builder. Plain sentences, "
    "no hype, no flattery, no bullet points. Reference only the evidence given; if it is "
    "thin, say less rather than inventing. Under 120 words, no subject line."
)


def _trace(entity_id: UUID, as_of: datetime) -> list[str]:
    lines = []
    for ev in reversed(store.events(as_of=as_of, entity_id=entity_id)):
        if ev.kind not in EVIDENCE_KINDS:
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        what = payload.get("repo") or payload.get("title") or payload.get("name") or str(ev.kind)
        lines.append(
            f"- {ev.observed_at:%Y-%m-%d} [{ev.kind}] {what} ({ev.source_url or 'no url'})"
        )
        if len(lines) >= MAX_EVIDENCE:
            break
    return lines


def draft(entity_id: UUID, *, as_of: datetime | None = None) -> str:
    as_of = as_of or utcnow()
    trace = _trace(entity_id, as_of)
    if not trace:
        return ""  # no evidence, no email. We do not cold-pitch on nothing.

    name = (store.get_entity(entity_id) or {}).get("display_name") or "there"
    prompt = (
        f"Draft an outreach email to {name}. Open by naming the single most specific "
        "thing they built from the evidence trace below, then one line on why we are "
        "paying attention, then one question they would enjoy answering."
    )
    out = llm.complete(prompt, system=SYSTEM, tier="fast", untrusted="\n".join(trace))
    return out if isinstance(out, str) else str(out)
