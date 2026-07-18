"""Entity resolution with deterministic aliases and cautious fuzzy signals.

Builder C's event-derived handle, co-occurrence, and temporal signals are layered
onto Builder A's canonical EventStore contract. This module never reaches into a
SQLite connection, so the same resolver works with either Memory backend.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

from rapidfuzz.distance import JaroWinkler
from unidecode import unidecode

from memory import store
from schema.events import (
    EntityCandidate,
    Entity,
    Event,
    EventKind,
    Resolution,
    ResolutionStatus,
    Source,
    utcnow,
)

MERGE_THRESHOLD = 0.85
NEW_THRESHOLD = 0.40
W_NAME = 0.60
W_COOC = 0.35

# C's event-derived scoring constants. Event co-occurrence is capped below a
# merge on name alone; an exact A context alias is stronger and retains A's
# name-plus-shared-context merge behavior.
W_EMAIL = 0.95
W_HANDLE = 0.60
W_EVENT_NAME = 0.55
W_EVENT_COOCCURRENCE = 0.25
TEMPORAL_PENALTY = 0.20
NAME_FLOOR = 0.80
NAME_CEIL = 0.95

HANDLE_KEYS = ("handle", "github", "github_login", "username", "hn_user", "author_handle")
CONTEXT_KEYS = ("repo", "repo_full_name", "hn_thread", "story_id", "paper_id", "arxiv_id", "doi")
_WIDE = utcnow() + timedelta(days=365 * 100)

_HANDLE_KIND = {
    "github": "handle:github",
    "twitter": "twitter",
    "x": "twitter",
    "linkedin": "linkedin",
    "hn": "hn",
}


def normalize_name(name: str) -> str:
    ascii_form = unidecode(name)
    return re.sub(r"[^a-z0-9]+", " ", ascii_form.casefold()).strip()


def name_similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    ordered = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    return max(JaroWinkler.similarity(na, nb), JaroWinkler.similarity(*ordered))


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def url_identity(url: str) -> tuple[str, str] | None:
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
        return ("url", f"{host}/{'/'.join(segs)}" if segs else host)
    return None


def _url_key(raw: str) -> str | None:
    value = re.sub(r"^www\.", "", re.sub(r"^https?://", "", raw.strip().lower())).rstrip("/")
    if not value:
        return None
    if match := re.fullmatch(r"[\w.-]+/user\?id=([a-z0-9][\w.-]*)", value):
        return f"handle:{match.group(1)}"
    if match := re.fullmatch(r"(?:github|twitter|x|medium)\.com/@?([a-z0-9][\w.-]*)", value):
        return f"handle:{match.group(1)}"
    if match := re.fullmatch(r"([a-z0-9][\w-]*)\.github\.io", value):
        return f"handle:{match.group(1)}"
    return f"url:{value}"


def _key_from_string(value: str) -> str | None:
    value = value.strip().lower()
    if not value or " " in value:
        return None
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", value):
        return f"mail:{value}"
    if "/" in value or "." in value:
        return _url_key(value)
    return None


def candidate_keys(candidate: EntityCandidate) -> set[str]:
    keys: set[str] = set()
    if candidate.email:
        keys.add(f"mail:{normalize_email(candidate.email)}")
    for url in candidate.urls:
        if key := _url_key(url):
            keys.add(key)
    for handle in candidate.handles.values():
        if handle.strip():
            keys.add(f"handle:{handle.strip().casefold().removeprefix('@')}")
    return keys


def _event_keys(event: Event) -> set[str]:
    keys: set[str] = set()
    if event.source_url and (key := _url_key(event.source_url)):
        keys.add(key)
    for key in HANDLE_KEYS:
        value = event.payload.get(key)
        if isinstance(value, str) and value.strip():
            keys.add(f"handle:{value.strip().casefold().removeprefix('@')}")
    for value in event.payload.values():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and (key := _key_from_string(item)):
                keys.add(key)
    return keys


def _event_contexts(event: Event) -> set[str]:
    return {
        f"ctx:{event.payload[key]}".lower()
        for key in CONTEXT_KEYS
        if isinstance(event.payload.get(key), str)
    }


def _candidate_contexts(candidate: EntityCandidate, context: list[tuple[str, str]]) -> set[str]:
    values = {f"ctx:{value}".lower() for _, value in context}
    for url in candidate.urls:
        parsed = urlparse(url if "//" in url else f"https://{url}")
        segs = [s for s in parsed.path.split("/") if s]
        if len(segs) >= 2:
            values.add(f"ctx:{segs[0]}/{segs[1]}".lower())
    return values


def _era(events: list[Event]) -> tuple[datetime, datetime] | None:
    if not events:
        return None
    stamps = [event.observed_at for event in events]
    return min(stamps), max(stamps)


def _disjoint(a: tuple[datetime, datetime] | None, b: tuple[datetime, datetime] | None) -> bool:
    return a is not None and b is not None and max(a[0], b[0]) > min(a[1], b[1])


def _name_component(similarity: float) -> float:
    ramp = (similarity - NAME_FLOOR) / (NAME_CEIL - NAME_FLOOR)
    return W_EVENT_NAME * max(0.0, min(1.0, ramp))


def _alias_keys(entity_id: UUID) -> set[str]:
    keys: set[str] = set()
    for alias in store.get_store().aliases_for(entity_id):
        if alias.kind == "email":
            keys.add(f"mail:{alias.value}")
        elif alias.kind == "context":
            keys.add(f"ctx:{alias.value}".lower())
        elif alias.kind.startswith("handle:") or alias.kind in {"twitter", "linkedin", "hn"}:
            keys.add(f"handle:{alias.value}")
        else:
            keys.add(f"url:{alias.value}")
    return keys


def _score_against(
    candidate: EntityCandidate,
    keys: set[str],
    contexts: set[str],
    era: tuple[datetime, datetime] | None,
    entity: Entity,
) -> tuple[float, list[str]]:
    entity_id = entity.entity_id
    entity_events = store.get_store().events(entity_id=entity_id, as_of=_WIDE)
    entity_keys = _alias_keys(entity_id)
    alias_contexts = {key for key in entity_keys if key.startswith("ctx:")}
    entity_contexts: set[str] = set(alias_contexts)
    for event in entity_events:
        entity_keys |= _event_keys(event)
        entity_contexts |= _event_contexts(event)

    shared = keys & entity_keys
    emails = {key for key in shared if key.startswith("mail:")}
    handles = {key for key in shared if key.startswith("handle:")}
    score = W_EMAIL if emails else W_HANDLE if handles else 0.0
    fired: list[str] = []
    if emails:
        fired.append("exact email match")
    elif handles:
        fired.append("shared handle/url")

    similarity = name_similarity(candidate.name, entity.display_name) if candidate.name else 0.0
    component = _name_component(similarity)
    if component > 0:
        score += component
        fired.append(f"name similarity {similarity:.2f}")

    overlap = contexts & entity_contexts
    if overlap:
        score += (
            0.35 if overlap & alias_contexts else min(W_EVENT_COOCCURRENCE, 0.10 * len(overlap))
        )
        fired.append(f"co-occurrence in {', '.join(sorted(c[4:] for c in overlap))}")
    if _disjoint(era, _era(entity_events)):
        score -= TEMPORAL_PENALTY
        fired.append("activity eras are disjoint (temporal penalty)")
    return max(0.0, min(1.0, score)), fired


def resolve(candidate: EntityCandidate) -> Resolution:
    s = store.get_store()
    strong, context = _candidate_aliases(candidate)
    name_norm = normalize_name(candidate.name) if candidate.name else ""

    matched: dict[UUID, list[str]] = {}
    for kind, value, _source in strong:
        if entity_id := s.find_by_alias(kind, value):
            matched.setdefault(entity_id, []).append(f"{kind}:exact")

    if len(matched) == 1:
        entity_id = next(iter(matched))
        _attach(s, entity_id, strong, context, candidate.source)
        signals = sorted(set(matched[entity_id]))
        rationale = f"exact identifier match ({', '.join(signals)})"
        if candidate.email:
            rationale = f"exact email match; {rationale}"
        if candidate.name and (entity := s.get_entity(entity_id)):
            rationale += (
                f"; name similarity {name_similarity(candidate.name, entity.display_name):.2f}"
            )
        result = Resolution(
            status=ResolutionStatus.MERGED,
            entity_id=entity_id,
            score=0.98,
            alternatives=[],
            rationale=rationale,
            signals=signals,
        )
        _emit_merge(s, candidate, result)
        return result

    if len(matched) > 1:
        ids = sorted(matched, key=str)
        primary, alternatives = ids[0], ids[1:]
        signals = sorted(
            {"conflicting_identifiers", *(s for values in matched.values() for s in values)}
        )
        for alternative in alternatives:
            s.record_merge(primary, alternative, "ambiguous", 0.5, "conflicting strong identifiers")
        result = Resolution(
            status=ResolutionStatus.AMBIGUOUS,
            entity_id=primary,
            score=0.5,
            alternatives=alternatives,
            rationale="conflicting strong identifiers point at different entities",
            signals=signals,
        )
        _emit_merge(s, candidate, result)
        return result

    keys = candidate_keys(candidate)
    contexts = _candidate_contexts(candidate, context)
    own_events = [event for event in s.events(as_of=_WIDE) if keys & _event_keys(event)]
    for event in own_events:
        contexts |= _event_contexts(event)
    era = _era(own_events)
    scored = [
        (*_score_against(candidate, keys, contexts, era, entity), entity) for entity in s.entities()
    ]
    scored.sort(key=lambda item: (-item[0], str(item[2].entity_id)))
    best_score, fired, best = scored[0] if scored else (0.0, [], None)

    if best is not None and best_score >= MERGE_THRESHOLD:
        _attach(s, best.entity_id, strong, context, candidate.source)
        result = Resolution(
            status=ResolutionStatus.MERGED,
            entity_id=best.entity_id,
            score=best_score,
            alternatives=[],
            rationale="; ".join(fired) or "corroborating identity signals",
            signals=fired,
        )
        _emit_merge(s, candidate, result)
        return result

    if best is None or best_score < NEW_THRESHOLD:
        entity = _create(s, candidate, name_norm, strong, context)
        rationale = "no sufficient match — new entity"
        if fired:
            rationale += ": " + "; ".join(fired)
        return Resolution(
            status=ResolutionStatus.NEW,
            entity_id=entity.entity_id,
            score=best_score,
            alternatives=[],
            rationale=rationale,
            signals=fired,
        )

    entity = _create(s, candidate, name_norm, strong, context)
    rationale = (
        "We could not confirm this is the same person; kept a separate identity "
        f"inside the {NEW_THRESHOLD}–{MERGE_THRESHOLD} band"
    )
    if fired:
        rationale += ": " + "; ".join(fired)
    s.record_merge(entity.entity_id, best.entity_id, "ambiguous", best_score, rationale)
    result = Resolution(
        status=ResolutionStatus.AMBIGUOUS,
        entity_id=entity.entity_id,
        score=best_score,
        alternatives=[best.entity_id],
        rationale=rationale,
        signals=fired,
    )
    _emit_merge(s, candidate, result)
    return result


def _candidate_aliases(
    candidate: EntityCandidate,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    source = str(candidate.source)
    strong: list[tuple[str, str, str]] = []
    context: list[tuple[str, str]] = []
    if candidate.email:
        strong.append(("email", normalize_email(candidate.email), source))
    for platform, handle in candidate.handles.items():
        kind = _HANDLE_KIND.get(platform.lower(), f"handle:{platform.lower()}")
        strong.append((kind, handle.casefold().removeprefix("@"), source))
    for url in candidate.urls:
        if ident := url_identity(url):
            kind, value = ident
            (context if kind == "context" else strong).append(
                (kind, value) if kind == "context" else (kind, value, source)
            )
    return strong, context


def _attach(
    s: store.EventStore,
    entity_id: UUID,
    strong: list[tuple[str, str, str]],
    context: list[tuple[str, str]],
    source: Source,
) -> None:
    for kind, value, source_name in strong:
        s.add_alias(entity_id, kind, value, source_name)
    for kind, value in context:
        s.add_alias(entity_id, kind, value, str(source))


def _create(
    s: store.EventStore,
    candidate: EntityCandidate,
    name_norm: str,
    strong: list[tuple[str, str, str]],
    context: list[tuple[str, str]],
) -> Entity:
    display = candidate.name or (strong[0][1] if strong else "unknown")
    entity = s.create_entity(display_name=display, name_normalized=name_norm or display.casefold())
    _attach(s, entity.entity_id, strong, context, candidate.source)
    return entity


def _emit_merge(s, candidate: EntityCandidate, result: Resolution) -> None:
    flags: list[str] = []
    if candidate.name and unidecode(candidate.name) != candidate.name:
        flags.append("transliterated_name")
    now = utcnow()
    s.append(
        Event(
            entity_id=result.entity_id,
            kind=EventKind.ENTITY_MERGE,
            source=Source.MANUAL,
            observed_at=now,
            ingested_at=now,
            payload={
                "status": result.status.value,
                "score": result.score,
                "signals": result.signals,
                "alternatives": [str(a) for a in result.alternatives],
                "rationale": result.rationale,
            },
            evidence_span=result.rationale,
            confidence=result.score,
            integrity_flags=flags,
        )
    )
