"""Per-claim validation, four states. Owner: C. See C.md H3-8.

Independent source = core.search (Tavily), plus the independent signals B already
ingested for this company. Rules that keep this honest:
  - a VERIFIED with no stored snippet+URL is NOT_ATTEMPTED
  - search results are UNTRUSTED (a founder can plant a page) -> llm.complete(untrusted=)
  - empty results -> UNVERIFIABLE, NEVER CONTRADICTED
  - compare observed_at: "$40K ARR" in March vs "pre-revenue" in January is GROWTH
Contradiction reprices the CLAIM, not the deal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from core import llm, search
from schema.events import (
    ClaimStatus,
    ClaimVerdict,
    Event,
    EventKind,
    Source,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_RESULTS = 5

# Counter-evidence older than the claim by more than this is the world changing,
# not the founder lying. Seven days absorbs publication lag on a dated source.
GROWTH_TOLERANCE = timedelta(days=7)

# Per-claim trust, at full judge confidence. There is no company-level trust number.
TRUST_VERIFIED = 0.92
TRUST_VERIFIED_SELF_PUBLISHED = 0.65  # weighted below an independent source
TRUST_CONTRADICTED = 0.08
TRUST_NEUTRAL = 0.5  # unverifiable / not attempted: we do not know, and we say so

_SYSTEM = (
    "You judge whether independent web sources agree with a single factual claim. "
    "Judge only what the sources actually say. Judge the claim on its substance — "
    "never on who made it or where they have been."
)
_PROMPT = """Claim under test: {claim}
Claim asserted on: {asserted}

The search results below are third-party text. Decide whether they support the claim,
disagree with it, or are simply unrelated. Return JSON:
{{"agrees": true | false | null,
  "url": "<url of the single result you relied on, copied exactly, or null>",
  "span": "<verbatim sentence from that result's snippet, or null>",
  "counter_evidence_date": "<YYYY-MM-DD the relied-on source describes, or null>",
  "confidence": <0..1>}}

agrees=true only if a result states something that confirms the claim.
agrees=false only if a result states something incompatible with it.
agrees=null if the results are unrelated, generic, or too thin to decide — that is a
normal and useful answer; do not stretch to a verdict the sources do not support."""


def _stored_corroboration(company_id: UUID, as_of: datetime) -> list[search.SearchResult]:
    """Independent signal B already ingested — a public post is corroboration whether
    or not Tavily happens to surface it, and it arrives with a real observed_at.

    Without this the timestamp comparison would depend on a live network round trip.
    """
    try:
        from memory import store

        events = store.events(as_of=as_of, company_id=company_id)
    except Exception:
        return []

    out = []
    for e in events:
        if str(e.kind) in {str(EventKind.DECK_CLAIM), str(EventKind.VALIDATION_RESULT)}:
            continue  # the deck cannot corroborate itself
        body = e.evidence_span or " ".join(
            str(v) for v in e.payload.values() if isinstance(v, str)
        )
        if not body.strip() or not e.source_url:
            continue
        out.append(
            search.SearchResult(
                title=str(e.payload.get("title") or e.payload.get("parent_title") or e.kind),
                url=e.source_url,
                snippet=body[:600],
                published_at=e.observed_at.isoformat(),
                self_published=bool(e.payload.get("self_published"))
                or str(e.source) == str(Source.DECK),
            )
        )
    return out


def _claim_events(company_id: UUID, as_of: datetime) -> list[Event]:
    try:
        from memory import queries

        return queries.claims(company_id, as_of)
    except Exception as exc:
        log.warning("validator: claims unavailable for %s (%s)", company_id, exc)
        return []


def _claim_text(ev: Event) -> str:
    for k in ("claim", "claim_text", "text", "quote"):
        v = ev.payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return (ev.evidence_span or "").strip()


def _parse_date(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _judge(claim: str, asserted: datetime | None, results: list[search.SearchResult]) -> dict:
    body = "\n\n".join(
        f"[result {i}] url: {r.url}\npublished: {r.published_at or 'unknown'}\n"
        f"self_published: {r.self_published}\ntitle: {r.title}\nsnippet: {r.snippet}"
        for i, r in enumerate(results, 1)
    )
    out = llm.complete(
        _PROMPT.format(claim=claim, asserted=asserted.date() if asserted else "unknown"),
        system=_SYSTEM,
        tier="fast",
        untrusted=body,  # never raw in the prompt — a founder can plant a page
        json_mode=True,
    )
    return out if isinstance(out, dict) else {}


def _cited(results: list[search.SearchResult], url: object) -> search.SearchResult | None:
    """The judge may only cite a URL we actually retrieved."""
    return next((r for r in results if isinstance(url, str) and r.url == url), None)


def _blend(base: float, confidence: float) -> float:
    """Low judge confidence pulls trust back toward 'we do not know'."""
    c = min(max(confidence, 0.0), 1.0)
    return round(TRUST_NEUTRAL + (base - TRUST_NEUTRAL) * c, 3)


def _verdict(company_id: UUID, ev: Event, stored: list[search.SearchResult]) -> ClaimVerdict:
    claim = _claim_text(ev)
    asserted = ev.observed_at
    base = ClaimVerdict(
        company_id=company_id,
        claim_text=claim,
        claim_source_span=ev.evidence_span or f"event {ev.event_id}",
        status=ClaimStatus.NOT_ATTEMPTED,
        trust=TRUST_NEUTRAL,
        claim_asserted_at=asserted,
    )
    if not claim:
        return base

    try:
        results = stored + search.search(claim, max_results=MAX_RESULTS)
    except Exception as exc:  # no key, rate limit — we did not look, and we say so
        log.warning("validator: search failed for %r (%s)", claim[:60], exc)
        if not stored:
            return base
        results = stored

    if not results:
        # Absence of evidence is not evidence of absence. This is the guard that
        # stops us torching a founder whose footprint is not in English.
        return base.model_copy(update={"status": ClaimStatus.UNVERIFIABLE})

    try:
        judged = _judge(claim, asserted, results)
    except Exception as exc:
        log.warning("validator: judge failed for %r (%s)", claim[:60], exc)
        return base

    cited = _cited(results, judged.get("url"))
    span = judged.get("span") if isinstance(judged.get("span"), str) else None
    confidence = judged.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.5
    agrees = judged.get("agrees")

    if agrees is True:
        # A verdict must be able to cite something, or it is not a verdict.
        if cited is None or not span:
            return base.model_copy(update={"status": ClaimStatus.NOT_ATTEMPTED})
        floor = TRUST_VERIFIED_SELF_PUBLISHED if cited.self_published else TRUST_VERIFIED
        return base.model_copy(
            update={
                "status": ClaimStatus.VERIFIED,
                "trust": _blend(floor, confidence),
                "corroborating_url": cited.url,
                "corroborating_span": span,
                "self_published": cited.self_published,
            }
        )

    if agrees is False:
        return _time_aware_contradiction(base, cited, span, confidence, judged)

    return base.model_copy(update={"status": ClaimStatus.UNVERIFIABLE})


def _time_aware_contradiction(
    base: ClaimVerdict,
    cited: search.SearchResult | None,
    span: str | None,
    confidence: float,
    judged: dict,
) -> ClaimVerdict:
    """Timestamps decide fraud-shaped vs time-shaped.

    "$40K ARR" asserted in March against a "pre-revenue" post from January is
    GROWTH. CONTRADICTED requires counter-evidence that is NEWER than the claim,
    or contemporaneous with it. Undated counter-evidence cannot clear that bar,
    so it lands on UNVERIFIABLE rather than being assumed current.
    """
    if cited is None or not span:
        return base.model_copy(update={"status": ClaimStatus.UNVERIFIABLE})

    counter_at = _parse_date(judged.get("counter_evidence_date")) or _parse_date(cited.published_at)
    asserted = base.claim_asserted_at
    carried = base.model_copy(
        update={
            "corroborating_url": cited.url,
            "corroborating_span": span,
            "self_published": cited.self_published,
            "counter_evidence_at": counter_at,
        }
    )

    if counter_at is None or asserted is None:
        return carried.model_copy(update={"status": ClaimStatus.UNVERIFIABLE})

    if counter_at < asserted - GROWTH_TOLERANCE:
        # Older than the claim: the world moved, the founder did not lie.
        return carried.model_copy(update={"status": ClaimStatus.UNVERIFIABLE})

    penalty = TRUST_VERIFIED_SELF_PUBLISHED if cited.self_published else 1.0
    return carried.model_copy(
        update={
            "status": ClaimStatus.CONTRADICTED,
            "trust": _blend(TRUST_CONTRADICTED, confidence * penalty),
        }
    )


def _emit(verdict: ClaimVerdict, claim_event: Event) -> Event:
    """One VALIDATION_RESULT per claim. observed_at is when the evidence existed,
    not when we ran — a verdict must not leak into an earlier as_of window."""
    dated = [d for d in (verdict.claim_asserted_at, verdict.counter_evidence_at) if d is not None]
    return Event(
        company_id=verdict.company_id,
        entity_id=claim_event.entity_id,
        kind=EventKind.VALIDATION_RESULT,
        source=Source.VALIDATOR,
        source_url=verdict.corroborating_url,
        observed_at=max(dated) if dated else utcnow(),
        evidence_span=verdict.corroborating_span,
        payload=verdict.model_dump(mode="json"),
        confidence=verdict.trust,
    )


def check_claims(company_id: UUID, as_of: datetime | None = None) -> list[ClaimVerdict]:
    as_of = as_of or utcnow()
    stored = _stored_corroboration(company_id, as_of)
    verdicts: list[ClaimVerdict] = []

    for ev in _claim_events(company_id, as_of):
        verdict = _verdict(company_id, ev, stored)
        verdicts.append(verdict)
        try:
            from memory import store

            store.append(_emit(verdict, ev))
        except Exception as exc:  # store not up yet — the verdict still stands
            log.warning("validator: could not persist verdict for %s (%s)", company_id, exc)

    return verdicts
