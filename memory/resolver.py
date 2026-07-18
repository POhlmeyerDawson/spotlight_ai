"""Entity resolution. Owner: A. See A.md H3-8.

AMBIGUOUS is the point: we never guess two identities into one. Type 6 lives here —
normalize + transliterate before fuzzy matching or non-Latin names silently vanish.

Signals, strongest first:
  1. exact identifier  — email / github / twitter / linkedin / personal-site URL. Deterministic.
  2. name similarity    — unidecode-transliterate, casefold, then Jaro-Winkler. Never enough on
                          its own to MERGE (two people share a name), so it maxes out below the
                          merge threshold and only clears it with a corroborating signal.
  3. co-occurrence      — a shared context (same repo, same thread). Corroborates a name match.

Three outcomes, and the third is the deliverable:
  MERGED (>= 0.85) · NEW (< 0.4) · AMBIGUOUS (between, or two strong ids pointing at two people).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from rapidfuzz.distance import JaroWinkler
from unidecode import unidecode

from memory import store
from schema.events import (
    EntityCandidate,
    Event,
    EventKind,
    Resolution,
    ResolutionStatus,
    Source,
    utcnow,
)

MERGE_THRESHOLD = 0.85
NEW_THRESHOLD = 0.40
W_NAME = 0.60  # a perfect name match alone -> 0.60, i.e. AMBIGUOUS, never MERGED
W_COOC = 0.35  # name + one corroborating context -> clears the merge threshold

_HANDLE_KIND = {
    "github": "handle:github",
    "twitter": "twitter",
    "x": "twitter",
    "linkedin": "linkedin",
    "hn": "hn",
}


# ---------------------------------------------------------------------------
# Normalization — the Type 6 guarantee
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """unidecode transliterates any script to ASCII (Александр -> Aleksandr,
    Иванов -> Ivanov, 田中 -> Tian Zhong), then casefold + strip punctuation. Without
    this step a non-Latin name never matches its romanization and the founder
    silently disappears from the graph."""
    ascii_form = unidecode(name)
    collapsed = re.sub(r"[^a-z0-9]+", " ", ascii_form.casefold())
    return collapsed.strip()


def name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler on the transliterated, normalized forms, in [0, 1]."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return JaroWinkler.similarity(na, nb)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def url_identity(url: str) -> tuple[str, str] | None:
    """Map a URL to an (alias_kind, value). Profile URLs become strong identity
    aliases; a multi-segment repo URL becomes a 'context' alias (co-occurrence)."""
    parsed = urlparse(url if "//" in url else f"https://{url}")
    host = parsed.netloc.lower().removeprefix("www.")
    segs = [s for s in parsed.path.split("/") if s]
    if host.endswith(".github.io"):
        return ("handle:github", host.removesuffix(".github.io"))
    if host == "github.com" and segs:
        if len(segs) == 1:
            return ("handle:github", segs[0].casefold())
        return ("context", f"github:{segs[0].casefold()}/{segs[1].casefold()}")
    if host in ("twitter.com", "x.com") and segs:
        return ("twitter", segs[0].casefold().removeprefix("@"))
    if host == "linkedin.com" and len(segs) >= 2 and segs[0] == "in":
        return ("linkedin", segs[1].casefold())
    if host:
        path = "/".join(segs)
        return ("url", f"{host}/{path}" if path else host)
    return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve(candidate: EntityCandidate) -> Resolution:
    s = store.get_store()
    strong, context = _candidate_aliases(candidate)
    name_norm = normalize_name(candidate.name) if candidate.name else ""

    # 1) Deterministic identifier match.
    matched: dict = {}  # entity_id -> [signals]
    for kind, value, _src in strong:
        eid = s.find_by_alias(kind, value)
        if eid is not None:
            matched.setdefault(eid, []).append(f"{kind}:exact")

    if len(matched) == 1:
        eid = next(iter(matched))
        _attach(s, eid, strong, context, candidate.source)
        signals = sorted(set(matched[eid]))
        res = Resolution(
            status=ResolutionStatus.MERGED,
            entity_id=eid,
            score=0.98,
            alternatives=[],
            rationale=f"exact identifier match ({', '.join(signals)})",
            signals=signals,
        )
        _emit_merge(s, candidate, res)
        return res

    if len(matched) > 1:
        # Two strong identifiers point at two different people. We do NOT fuse
        # them — that's the accidental-merge failure mode. Surface it.
        ids = sorted(matched, key=str)
        primary, alts = ids[0], ids[1:]
        signals = sorted({"conflicting_identifiers", *(s for v in matched.values() for s in v)})
        for alt in alts:
            s.record_merge(primary, alt, "ambiguous", 0.5, "conflicting strong identifiers")
        res = Resolution(
            status=ResolutionStatus.AMBIGUOUS,
            entity_id=primary,
            score=0.5,
            alternatives=alts,
            rationale="conflicting strong identifiers point at different entities",
            signals=signals,
        )
        _emit_merge(s, candidate, res)
        return res

    # 2) No strong match — score every existing entity on name + co-occurrence.
    context_values = {v for _k, v in context}
    best = None
    best_name = 0.0
    best_cooc = False
    best_combined = 0.0
    for ent in s.entities():
        nsim = name_similarity(candidate.name, ent.display_name) if candidate.name else 0.0
        cooc = _shares_context(s, ent.entity_id, context_values)
        combined = _combine(nsim, cooc)
        if combined > best_combined:
            best, best_name, best_cooc, best_combined = ent, nsim, cooc, combined

    signals = _fuzzy_signals(best_name, best_cooc)

    if best is not None and best_combined >= MERGE_THRESHOLD:
        _attach(s, best.entity_id, strong, context, candidate.source)
        res = Resolution(
            status=ResolutionStatus.MERGED,
            entity_id=best.entity_id,
            score=best_combined,
            alternatives=[],
            rationale=f"name {best_name:.2f} + corroborating context",
            signals=signals,
        )
        _emit_merge(s, candidate, res)
        return res

    if best is None or best_combined < NEW_THRESHOLD:
        entity = _create(s, candidate, name_norm, strong, context)
        return Resolution(
            status=ResolutionStatus.NEW,
            entity_id=entity.entity_id,
            score=best_combined,
            alternatives=[],
            rationale="no sufficient match — new entity",
            signals=signals,
        )

    # 3) In-between — keep BOTH nodes, record the doubt, let D surface it.
    entity = _create(s, candidate, name_norm, strong, context)
    s.record_merge(
        entity.entity_id, best.entity_id, "ambiguous", best_combined, "uncertain name match"
    )
    res = Resolution(
        status=ResolutionStatus.AMBIGUOUS,
        entity_id=entity.entity_id,
        score=best_combined,
        alternatives=[best.entity_id],
        rationale=f"uncertain match (name {best_name:.2f}) — not merged, flagged for review",
        signals=signals,
    )
    _emit_merge(s, candidate, res)
    return res


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _candidate_aliases(
    candidate: EntityCandidate,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """(strong identity aliases, context aliases). Each strong entry is
    (kind, normalized_value, source)."""
    src = str(candidate.source)
    strong: list[tuple[str, str, str]] = []
    context: list[tuple[str, str]] = []

    if candidate.email:
        strong.append(("email", normalize_email(candidate.email), src))
    for platform, handle in candidate.handles.items():
        kind = _HANDLE_KIND.get(platform.lower(), f"handle:{platform.lower()}")
        strong.append((kind, handle.casefold().removeprefix("@"), src))
    for url in candidate.urls:
        ident = url_identity(url)
        if ident is None:
            continue
        kind, value = ident
        if kind == "context":
            context.append((kind, value))
        else:
            strong.append((kind, value, src))
    return strong, context


def _combine(name_sim: float, cooccurs: bool) -> float:
    return min(1.0, W_NAME * name_sim + (W_COOC if cooccurs else 0.0))


def _fuzzy_signals(name_sim: float, cooccurs: bool) -> list[str]:
    signals = []
    if name_sim > 0:
        signals.append(f"name_sim:{name_sim:.2f}")
    if cooccurs:
        signals.append("shared_context")
    return signals


def _shares_context(s: store.EventStore, entity_id, context_values: set[str]) -> bool:
    if not context_values:
        return False
    return any(a.value in context_values for a in s.aliases_for(entity_id) if a.kind == "context")


def _attach(
    s: store.EventStore,
    entity_id,
    strong: list[tuple[str, str, str]],
    context: list[tuple[str, str]],
    source: Source,
) -> None:
    for kind, value, src in strong:
        s.add_alias(entity_id, kind, value, src)
    for kind, value in context:
        s.add_alias(entity_id, kind, value, str(source))


def _create(
    s: store.EventStore,
    candidate: EntityCandidate,
    name_norm: str,
    strong: list[tuple[str, str, str]],
    context: list[tuple[str, str]],
):
    display = candidate.name or (strong[0][1] if strong else "unknown")
    entity = s.create_entity(display_name=display, name_normalized=name_norm or display.casefold())
    _attach(s, entity.entity_id, strong, context, candidate.source)
    return entity


def _emit_merge(s: store.EventStore, candidate: EntityCandidate, res: Resolution) -> None:
    """A resolution decision is itself an event — D's trace can show why two
    identities were (or weren't) fused. Stamped at decision time; it's a
    system fact, and it's excluded from any historical as_of read before now."""
    flags: list[str] = []
    if candidate.name and unidecode(candidate.name) != candidate.name:
        flags.append("transliterated_name")
    now = utcnow()
    s.append(
        Event(
            entity_id=res.entity_id,
            kind=EventKind.ENTITY_MERGE,
            source=Source.MANUAL,
            observed_at=now,
            ingested_at=now,
            payload={
                "status": res.status.value,
                "score": res.score,
                "signals": res.signals,
                "alternatives": [str(a) for a in res.alternatives],
            },
            evidence_span=res.rationale,
            confidence=res.score,
            integrity_flags=flags,
        )
    )
