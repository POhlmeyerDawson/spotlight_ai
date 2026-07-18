"""Entity resolution. Owner: A. See A.md H3-8.

AMBIGUOUS is the point: we never guess two identities into one. Type 6 lives here —
normalize + transliterate before fuzzy matching or non-Latin names silently vanish.

Four signals, summed, then clamped:
    identity   exact email 0.95 (decisive on its own) | shared url/handle 0.60
    name       up to 0.55, Jaro-Winkler over transliterated names
    co-occur   up to 0.25, shared repo / HN thread / paper
    temporal  -0.20 when the two activity eras do not overlap at all

A name alone therefore tops out at 0.55 — deliberately inside the AMBIGUOUS band. Two
real people called "Wei Zhang" must never merge just because rapidfuzz says 1.0.

Every decision is written twice: a row in `merges` (the decision ledger) and, for
merged/ambiguous, an ENTITY_MERGE event (what D reads to surface "we could not
confirm these are the same person" in the memo).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from rapidfuzz.distance import JaroWinkler
from unidecode import unidecode

from memory import db, store
from schema.events import (
    EntityCandidate,
    Event,
    EventKind,
    Resolution,
    ResolutionStatus,
    utcnow,
)

MERGE_THRESHOLD = 0.85
NEW_THRESHOLD = 0.40

W_EMAIL = 0.95
W_HANDLE = 0.60
W_NAME = 0.55
W_COOCCURRENCE = 0.25  # cap; 0.10 per shared context
TEMPORAL_PENALTY = 0.20

# Below this Jaro-Winkler the names contribute nothing; at NAME_CEIL they contribute W_NAME.
NAME_FLOOR = 0.80
NAME_CEIL = 0.95

# Payload keys that carry a bare platform handle rather than a URL.
HANDLE_KEYS = ("handle", "github", "github_login", "username", "hn_user", "author_handle")

# Payload keys that name a shared context two identities can co-occur in.
CONTEXT_KEYS = ("repo", "repo_full_name", "hn_thread", "story_id", "paper_id", "arxiv_id", "doi")

# Resolution is an identity question, not a scoring one: it reads the whole log, not a
# prefix of it. as_of stays required by store.events(), so we pass an explicit far future.
_WIDE = utcnow() + timedelta(days=365 * 100)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Transliterate to ASCII, then strip everything that isn't a name. Type 6 starts here."""
    ascii_name = unidecode(name).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_name)).strip()


def name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler over transliterated names, order-invariant on the tokens."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    sorted_a, sorted_b = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    return max(JaroWinkler.similarity(na, nb), JaroWinkler.similarity(sorted_a, sorted_b))


def _url_key(raw: str) -> str | None:
    """github.com/x, x.github.io, twitter/x and HN user links all collapse to handle:x."""
    u = re.sub(r"^www\.", "", re.sub(r"^https?://", "", raw.strip().lower())).rstrip("/")
    if not u:
        return None
    # HN user links: <host>/user?id=<handle>. Host left generic on purpose — Invariant #3
    # bans the literal name anywhere in source, and it buys nothing here.
    if m := re.fullmatch(r"[\w.-]+/user\?id=([a-z0-9][\w.-]*)", u):
        return f"handle:{m.group(1)}"
    # single path segment only — github.com/org/repo names a repo, not a person
    if m := re.fullmatch(r"(?:github|twitter|x|medium)\.com/@?([a-z0-9][\w.-]*)", u):
        return f"handle:{m.group(1)}"
    if m := re.fullmatch(r"([a-z0-9][\w-]*)\.github\.io", u):
        return f"handle:{m.group(1)}"
    return f"url:{u}"


def _key_from_string(value: str) -> str | None:
    """Best-effort identity key for a free-floating string found in an event payload."""
    v = value.strip().lower()
    if not v or " " in v:
        return None
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v):
        return f"mail:{v}"
    if "/" in v or "." in v:
        return _url_key(v)
    return None


def candidate_keys(candidate: EntityCandidate) -> set[str]:
    keys = set()
    if candidate.email:
        keys.add(f"mail:{candidate.email.strip().lower()}")
    for url in candidate.urls:
        if key := _url_key(url):
            keys.add(key)
    for handle in candidate.handles.values():
        if handle.strip():
            keys.add(f"handle:{handle.strip().lower()}")
    return keys


def _event_keys(event: Event) -> set[str]:
    keys = set()
    if event.source_url and (key := _url_key(event.source_url)):
        keys.add(key)
    for k in HANDLE_KEYS:
        if isinstance(event.payload.get(k), str) and event.payload[k].strip():
            keys.add(f"handle:{event.payload[k].strip().lower()}")
    for value in event.payload.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and (key := _key_from_string(item)):
                keys.add(key)
    return keys


def _event_contexts(event: Event) -> set[str]:
    return {
        f"ctx:{event.payload[k]}".lower()
        for k in CONTEXT_KEYS
        if isinstance(event.payload.get(k), str)
    }


# ---------------------------------------------------------------------------
# Alias ledger — what makes resolution accumulate instead of re-deciding each time
# ---------------------------------------------------------------------------


def _aliases_for(entity_id: UUID) -> set[str]:
    rows = db.connect().execute(
        "select kind, value from entity_aliases where entity_id = ?", (str(entity_id),)
    )
    return {f"{r['kind']}:{r['value']}" for r in rows}


def _record_aliases(entity_id: UUID, keys: set[str], source: str) -> None:
    conn = db.connect()
    for key in keys:
        kind, _, value = key.partition(":")
        conn.execute(
            "insert or ignore into entity_aliases (alias_id, entity_id, kind, value, source) "
            "values (?,?,?,?,?)",
            (str(uuid4()), str(entity_id), kind, value, source),
        )
    conn.commit()


def _record_merge(
    entity_a: UUID, entity_b: UUID, status: str, score: float, rationale: str
) -> None:
    conn = db.connect()
    conn.execute(
        "insert into merges (merge_id, entity_a, entity_b, status, score, rationale, decided_at) "
        "values (?,?,?,?,?,?,?)",
        (str(uuid4()), str(entity_a), str(entity_b), status, score, rationale, db.to_iso(utcnow())),
    )
    conn.commit()


def _merged_away() -> set[str]:
    """Identities already absorbed into another. They are tombstones, not match candidates."""
    rows = db.connect().execute("select entity_b from merges where status = 'merged'")
    return {r["entity_b"] for r in rows}


def _create_entity(display_name: str, normalized: str) -> UUID:
    """Always a fresh row. store.upsert_entity() dedupes on the normalized name, which is
    exactly the guess this module exists to avoid — two distinct Wei Zhangs need two rows."""
    entity_id = uuid4()
    conn = db.connect()
    conn.execute(
        "insert into entities (entity_id, display_name, name_normalized, created_at) "
        "values (?,?,?,?)",
        (str(entity_id), display_name, normalized, db.to_iso(utcnow())),
    )
    conn.commit()
    return entity_id


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def _era(events: list[Event]) -> tuple[datetime, datetime] | None:
    if not events:
        return None
    stamps = [e.observed_at for e in events]
    return min(stamps), max(stamps)


def _disjoint(a: tuple[datetime, datetime] | None, b: tuple[datetime, datetime] | None) -> bool:
    return a is not None and b is not None and max(a[0], b[0]) > min(a[1], b[1])


def _name_component(similarity: float) -> float:
    ramp = (similarity - NAME_FLOOR) / (NAME_CEIL - NAME_FLOOR)
    return W_NAME * max(0.0, min(1.0, ramp))


def _score_against(
    candidate: EntityCandidate,
    keys: set[str],
    contexts: set[str],
    era: tuple[datetime, datetime] | None,
    entity: dict,
) -> tuple[float, list[str]]:
    entity_id = UUID(entity["entity_id"])
    entity_events = store.events(as_of=_WIDE, entity_id=entity_id)
    entity_keys = _aliases_for(entity_id)
    entity_contexts: set[str] = set()
    for event in entity_events:
        entity_keys |= _event_keys(event)
        entity_contexts |= _event_contexts(event)

    score = 0.0
    fired: list[str] = []

    shared = keys & entity_keys
    emails = {k for k in shared if k.startswith("mail:")}
    handles = {k for k in shared if not k.startswith("mail:")}
    if emails:
        score += W_EMAIL
        fired.append(f"exact email match ({', '.join(sorted(k[5:] for k in emails))})")
    elif handles:
        score += W_HANDLE
        fired.append(f"shared handle/url ({', '.join(sorted(handles))})")

    if candidate.name:
        similarity = name_similarity(candidate.name, entity["display_name"])
        component = _name_component(similarity)
        if component > 0:
            score += component
            fired.append(
                f"transliterated name similarity {similarity:.2f} "
                f"({candidate.name!r} ~ {entity['display_name']!r})"
            )

    overlap = contexts & entity_contexts
    if overlap:
        score += min(W_COOCCURRENCE, 0.10 * len(overlap))
        fired.append(f"co-occurrence in {', '.join(sorted(c[4:] for c in overlap))}")

    score = min(1.0, score)
    if _disjoint(era, _era(entity_events)):
        score -= TEMPORAL_PENALTY
        fired.append("activity eras are disjoint (temporal penalty)")

    return max(0.0, score), fired


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def resolve(candidate: EntityCandidate) -> Resolution:
    keys = candidate_keys(candidate)
    display_name = candidate.name or (sorted(keys)[0] if keys else "unknown")
    normalized = normalize_name(display_name)

    # The candidate's own footprint: every event carrying one of its identity keys.
    own_events = [e for e in store.events(as_of=_WIDE) if keys & _event_keys(e)]
    contexts: set[str] = set()
    for event in own_events:
        contexts |= _event_contexts(event)
    era = _era(own_events)

    tombstoned = _merged_away()
    scored = [
        (score, fired, entity)
        for entity in store.all_entities()
        if entity["entity_id"] not in tombstoned
        for score, fired in [_score_against(candidate, keys, contexts, era, entity)]
    ]
    scored.sort(key=lambda s: s[0], reverse=True)
    best_score, best_fired, best_entity = scored[0] if scored else (0.0, [], None)

    # The candidate always gets a row. On MERGED it becomes a tombstone pointing at the
    # survivor, so the merges ledger has two real ids on both sides.
    candidate_id = _create_entity(display_name, normalized)
    alternatives: list[UUID] = []

    if best_entity is not None and best_score > MERGE_THRESHOLD:
        status = ResolutionStatus.MERGED
        entity_id = UUID(best_entity["entity_id"])
        rationale = (
            f"Merged into {best_entity['display_name']!r} at {best_score:.2f}: "
            + "; ".join(best_fired)
        )
        _record_merge(entity_id, candidate_id, "merged", best_score, rationale)
    elif best_entity is not None and best_score >= NEW_THRESHOLD:
        status = ResolutionStatus.AMBIGUOUS
        entity_id = candidate_id
        in_band = [(s, e) for s, _, e in scored if s >= NEW_THRESHOLD]
        alternatives = [UUID(e["entity_id"]) for _, e in in_band]  # we do not pick one
        rationale = (
            "We could not confirm this is the same person as "
            + ", ".join(repr(e["display_name"]) for _, e in in_band)
            + f" ({best_score:.2f}, inside the {NEW_THRESHOLD}–{MERGE_THRESHOLD} band): "
            + "; ".join(best_fired)
            + ". Kept as a separate identity."
        )
        _record_merge(candidate_id, UUID(best_entity["entity_id"]), "ambiguous", best_score,
                      rationale)
    else:
        status = ResolutionStatus.NEW
        entity_id = candidate_id
        closest = (
            f" Closest existing identity {best_entity['display_name']!r} scored "
            f"{best_score:.2f}, below {NEW_THRESHOLD}."
            if best_entity is not None
            else ""
        )
        signals = f" Signals: {'; '.join(best_fired)}." if best_fired else ""
        rationale = (
            f"New identity {display_name!r}; nothing scored above {NEW_THRESHOLD}."
            f"{closest}{signals}"
        )
        if best_entity is not None:
            _record_merge(candidate_id, UUID(best_entity["entity_id"]), "rejected", best_score,
                          rationale)

    _record_aliases(entity_id, keys, str(candidate.source))

    if status is not ResolutionStatus.NEW:
        store.append(
            Event(
                entity_id=entity_id,
                kind=EventKind.ENTITY_MERGE,
                source=candidate.source,
                observed_at=utcnow(),
                payload={
                    "status": str(status),
                    "score": best_score,
                    "alternatives": [str(a) for a in alternatives],
                    "rationale": rationale,
                    "signals": best_fired,
                },
                confidence=best_score,
            )
        )

    return Resolution(
        status=status,
        entity_id=entity_id,
        score=best_score,
        alternatives=alternatives,
        rationale=rationale,
    )
