"""Fake entities + events so B/C/D have something to read before the real
pipeline lands. Owner: A. Not the demo fixtures (those are D's data/seed/) — this
is the minimal in-memory spine seed the unblock depends on.

Three archetypes, chosen to exercise the parts teammates build against:
  * a visible builder with a rising green-flag trajectory + a proof event
    (D's moving-score line, C's observation flow),
  * a cold-start founder with a deck claim and no public signal
    (the score correctly stays at the wide prior — the Proof Protocol beat),
  * an international founder whose name only matches after transliteration
    (the Type 6 guarantee), with a date-inferred, transliterated-name event.

One green flag is contradicted, so the contradiction boundary in the scorer is
exercised by real seed data, not only by a unit test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from memory import store
from schema.events import Event, EventKind, Source

_T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _at(month: int) -> datetime:
    return _T0 + timedelta(days=30 * month)


def seed(target: store.EventStore | None = None) -> dict:
    """Populate `target` (default: the module-global store). Returns the ids it
    created so callers build against real values instead of hardcoding."""
    s = target or store.get_store()
    events: list[UUID] = []

    def emit(event: Event) -> UUID:
        eid = s.append(event)
        events.append(eid)
        return eid

    # -- Type 1: visible builder, rising trajectory -------------------------
    ada = s.create_entity("Ada Okafor", "ada okafor")
    s.add_alias(ada.entity_id, "handle:github", "adaokafor", "github")
    s.add_alias(ada.entity_id, "email", "ada@infergrid.dev", "manual")
    infergrid = s.create_company("InferGrid", founder_entity_ids=[ada.entity_id], archetype=1)

    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            source_url="https://github.com/adaokafor/infergrid",
            observed_at=_at(0),
            payload={"repo": "infergrid", "commits": 42},
            evidence_span="commit 9f3a1c",
        )
    )
    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.RELEASE,
            source=Source.GITHUB,
            observed_at=_at(3),
            payload={"tag": "v0.1.0"},
            evidence_span="release v0.1.0",
        )
    )
    # Green flags, rising: shipped something users touch, then iterated on it.
    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.MANUAL,
            observed_at=_at(2),
            payload={"value": 0.6, "self_consistency": 0.8, "rule_id": "ships_users_touch"},
            confidence=0.8,
        )
    )
    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.MANUAL,
            observed_at=_at(5),
            payload={"value": 0.72, "self_consistency": 0.85, "rule_id": "iteration_velocity"},
            confidence=0.85,
        )
    )
    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.MANUAL,
            observed_at=_at(8),
            payload={"value": 0.8, "self_consistency": 0.9, "rule_id": "handles_ambiguity"},
            confidence=0.9,
        )
    )
    # A proof event — fresh, verified, behavioral -> low noise, moves the score.
    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.PROOF_ARTIFACT,
            source=Source.PROOF_PROTOCOL,
            observed_at=_at(11),
            payload={"value": 0.9, "self_consistency": 0.95},
            evidence_span="challenge #7 artifact",
        )
    )
    # A weak, later-contradicted green flag: the scorer must drop it.
    vanity = emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.WEB,
            observed_at=_at(6),
            payload={"value": 0.2, "self_consistency": 0.4, "rule_id": "follower_spike"},
            confidence=0.4,
        )
    )
    emit(
        Event(
            entity_id=ada.entity_id,
            company_id=infergrid.company_id,
            kind=EventKind.CONTRADICTION,
            source=Source.VALIDATOR,
            observed_at=_at(7),
            payload={"target_event_id": str(vanity), "reason": "vanity metric"},
            evidence_span="follower spike not corroborated",
        )
    )

    # -- Type 2: cold start, deck only, zero public signal ------------------
    mara = s.create_entity("Mara Lindqvist", "mara lindqvist")
    s.add_alias(mara.entity_id, "email", "mara@stealthkernel.io", "deck")
    stealth = s.create_company("StealthKernel", founder_entity_ids=[mara.entity_id], archetype=2)
    emit(
        Event(
            entity_id=mara.entity_id,
            company_id=stealth.company_id,
            kind=EventKind.DECK_CLAIM,
            source=Source.DECK,
            observed_at=_at(10),
            payload={"claim": "10x faster kernel scheduling"},
            evidence_span="slide 4",
            confidence=0.5,
        )
    )

    # -- Type 6: invisible international, transliterated name ----------------
    alex = s.create_entity("Александр Иванов", "aleksandr ivanov")
    s.add_alias(alex.entity_id, "handle:github", "aivanov", "github")
    vectormind = s.create_company("VectorMind", founder_entity_ids=[alex.entity_id], archetype=6)
    emit(
        Event(
            entity_id=alex.entity_id,
            company_id=vectormind.company_id,
            kind=EventKind.PROFILE_FACT,
            source=Source.WEB,
            source_url="https://habr.com/ru/users/aivanov",
            observed_at=_at(4),
            payload={"key": "bio", "value": "compiler engineer"},
            evidence_span="habr profile",
            integrity_flags=["transliterated_name", "date_inferred"],
        )
    )
    emit(
        Event(
            entity_id=alex.entity_id,
            company_id=vectormind.company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.GITHUB,
            observed_at=_at(6),
            payload={"value": 0.65, "self_consistency": 0.75, "rule_id": "technical_depth"},
            confidence=0.75,
        )
    )
    emit(
        Event(
            entity_id=alex.entity_id,
            company_id=vectormind.company_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.GITHUB,
            observed_at=_at(9),
            payload={"value": 0.78, "self_consistency": 0.8, "rule_id": "iteration_velocity"},
            confidence=0.8,
        )
    )

    return {
        "entities": {
            "ada": ada.entity_id,
            "mara": mara.entity_id,
            "alex": alex.entity_id,
        },
        "companies": {
            "infergrid": infergrid.company_id,
            "stealthkernel": stealth.company_id,
            "vectormind": vectormind.company_id,
        },
        "event_ids": events,
    }
