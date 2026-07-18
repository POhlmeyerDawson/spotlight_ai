"""RED-phase tests for the locked proof challenge and grading contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from intelligence import proof
from schema.events import Event, EventKind, Source

T0 = datetime(2025, 4, 5, 15, 30, tzinfo=timezone.utc)
COMPANY_ID = uuid4()
ENTITY_ID = uuid4()
ISSUED_EVENTS: list[Event] = []


@pytest.fixture(autouse=True)
def _durable_proof_store(monkeypatch):
    ISSUED_EVENTS.clear()
    monkeypatch.setattr(
        "memory.store.append", lambda event: ISSUED_EVENTS.append(event) or event.event_id
    )
    monkeypatch.setattr(
        "memory.store.events",
        lambda **kwargs: [
            event
            for event in ISSUED_EVENTS
            if event.kind == kwargs.get("kind") and event.observed_at <= kwargs["as_of"]
        ],
    )


def _claim(*, confidence: float, minute: int, text: str) -> Event:
    return Event(
        company_id=COMPANY_ID,
        entity_id=ENTITY_ID,
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        observed_at=T0.replace(minute=minute),
        payload={"claim": text},
        evidence_span=f"slide {minute}",
        confidence=confidence,
    )


def _good_answer() -> dict[str, str]:
    return {
        "prompt": "Build a bounded demonstration and explain the tradeoffs.",
        "central_claim": "Requests finish within the stated target.",
        "ambiguous_requirement": "Choose and state the expected request mix.",
        "planted_bad_constraint": "Use one worker while requiring parallel execution.",
    }


def test_generate_scopes_claim_read_and_selects_highest_confidence_latest_tie(monkeypatch) -> None:
    older_high = _claim(confidence=0.9, minute=10, text="older high claim")
    newer_high = _claim(confidence=0.9, minute=20, text="newer high claim")
    lower = _claim(confidence=0.4, minute=25, text="lower claim")
    calls: list[dict] = []
    seen: dict = {}
    monkeypatch.setattr(proof, "utcnow", lambda: T0)

    def fake_events(**kwargs):
        calls.append(kwargs)
        return [older_high, lower, newer_high]

    def judge(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return _good_answer()

    monkeypatch.setattr("memory.store.events", fake_events)
    challenge = proof.generate(COMPANY_ID, judge=judge)

    assert calls == [{"company_id": COMPANY_ID, "kind": EventKind.DECK_CLAIM, "as_of": T0}]
    assert "newer high claim" not in seen["prompt"]
    assert "newer high claim" in seen["untrusted"]
    assert "older high claim" not in seen["untrusted"]
    assert seen["json_mode"] is True
    assert challenge.company_id == COMPANY_ID
    assert challenge.central_claim == "newer high claim"
    assert challenge.issued_at == T0
    assert ISSUED_EVENTS[-1].kind is EventKind.PROOF_CHALLENGE_ISSUED
    assert ISSUED_EVENTS[-1].payload["challenge_id"] == str(challenge.challenge_id)
    assert all(
        value.strip()
        for value in (
            challenge.prompt,
            challenge.central_claim,
            challenge.ambiguous_requirement,
            challenge.planted_bad_constraint,
        )
    )


@pytest.mark.parametrize("answer", [RuntimeError("offline"), "not json", {}, {"prompt": ""}])
def test_generate_failure_returns_deterministic_nonempty_fallback(monkeypatch, answer) -> None:
    monkeypatch.setattr(proof, "utcnow", lambda: T0)
    monkeypatch.setattr(
        "memory.store.events", lambda **kwargs: [_claim(confidence=1, minute=5, text="claim")]
    )

    def judge(*args, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    first = proof.generate(COMPANY_ID, judge=judge)
    second = proof.generate(COMPANY_ID, judge=judge)

    def fields(challenge):
        return (
            challenge.prompt,
            challenge.central_claim,
            challenge.ambiguous_requirement,
            challenge.planted_bad_constraint,
        )

    assert fields(first) == fields(second)
    assert first.company_id == COMPANY_ID
    assert first.issued_at == T0
    assert all(value.strip() for value in fields(first))


def test_generate_without_claim_does_not_call_judge(monkeypatch) -> None:
    monkeypatch.setattr(proof, "utcnow", lambda: T0)
    monkeypatch.setattr("memory.store.events", lambda **kwargs: [])

    def judge(*args, **kwargs):
        raise AssertionError("judge must not run")

    challenge = proof.generate(COMPANY_ID, judge=judge)
    assert challenge.company_id == COMPANY_ID
    assert all(
        value.strip()
        for value in (
            challenge.prompt,
            challenge.central_claim,
            challenge.ambiguous_requirement,
            challenge.planted_bad_constraint,
        )
    )


def _trace(**updates) -> dict:
    trace = {
        "company_id": str(COMPANY_ID),
        "entity_id": str(ENTITY_ID),
        "completed_at": T0.isoformat(),
        "source_url": "https://example.test/artifact",
        "works": True,
        "sound": True,
        "handled_ambiguity": True,
        "challenged_bad_constraint": True,
        "asked_clarifying": False,
        "iteration_count": 4,
        "time_to_first_commit_min": 12.5,
        "latency_profile": [2.0, 4.0, 3.0],
        "events": [
            {"type": "clarifying_question", "at": (T0 - timedelta(minutes=50)).isoformat()},
            {"type": "constraint_challenged", "at": (T0 - timedelta(minutes=45)).isoformat()},
            {"type": "commit", "at": (T0 - timedelta(minutes=40)).isoformat()},
            {"type": "commit", "at": (T0 - timedelta(minutes=20)).isoformat()},
        ],
        "test_results": {"passed": 3, "total": 3, "static_checks_passed": True},
    }
    trace.update(updates)
    return trace


def _issued_challenge(*, entity_id=ENTITY_ID):
    challenge_id = uuid4()
    ISSUED_EVENTS.append(
        Event(
            company_id=COMPANY_ID,
            entity_id=entity_id,
            kind=EventKind.PROOF_CHALLENGE_ISSUED,
            source=Source.PROOF_PROTOCOL,
            observed_at=T0 - timedelta(minutes=60),
            payload={"challenge_id": str(challenge_id)},
        )
    )
    return challenge_id


def test_grade_emits_exact_two_events_with_locked_payloads_and_metadata() -> None:
    challenge_id = _issued_challenge()
    events = proof.grade(challenge_id, "working artifact", _trace())

    assert [event.kind for event in events] == [EventKind.PROOF_ARTIFACT, EventKind.PROOF_BEHAVIOR]
    artifact, behavior = events
    assert {
        "challenge_id": str(challenge_id),
        "artifact": "working artifact",
        "works": True,
        "sound": True,
        "handled_ambiguity": True,
    }.items() <= artifact.payload.items()
    assert {
        "challenge_id": str(challenge_id),
        "challenged_bad_constraint": True,
        "asked_clarifying": True,
        "iteration_count": 2,
        "time_to_first_commit_min": 20.0,
        "latency_profile": [20.0],
    }.items() <= behavior.payload.items()
    for event in (artifact, behavior):
        assert event.payload["value"] == event.payload["y"]
        assert event.payload["components"]
        assert event.payload["caveat"]
    assert artifact.confidence == 0.9
    assert behavior.confidence == 0.95
    for event in events:
        assert event.company_id == COMPANY_ID
        assert event.entity_id == ENTITY_ID
        assert event.source is Source.PROOF_PROTOCOL
        assert event.source_url == "https://example.test/artifact"
        assert event.observed_at == T0
        assert event.evidence_span


def test_grade_derives_behavior_from_raw_trace_not_summary_assertions() -> None:
    challenge_id = _issued_challenge()
    trace = _trace(
        works="true",
        sound=1,
        handled_ambiguity=None,
        challenged_bad_constraint="yes",
        asked_clarifying=0,
        events=[{"type": "assumption_stated", "at": (T0 - timedelta(minutes=10)).isoformat()}],
        test_results={"passed": 0, "total": 2, "static_checks_passed": False},
    )
    first = proof.grade(challenge_id, "artifact", trace)
    second = proof.grade(challenge_id, "artifact", trace)
    assert [e.model_dump(exclude={"event_id", "ingested_at"}) for e in first] == [
        e.model_dump(exclude={"event_id", "ingested_at"}) for e in second
    ]
    assert first[0].payload["works"] is False
    assert first[0].payload["sound"] is False
    assert first[0].payload["handled_ambiguity"] is True
    assert first[1].payload["challenged_bad_constraint"] is False
    assert first[1].payload["asked_clarifying"] is False
    assert first[1].payload["iteration_count"] == 0
    assert first[1].payload["time_to_first_commit_min"] is None
    assert first[1].payload["latency_profile"] == []


@pytest.mark.parametrize(
    "trace",
    [
        _trace(company_id=str(uuid4())),
        _trace(completed_at=None),
        _trace(completed_at="2025-04-05T15:30:00"),
        _trace(events=None),
        _trace(test_results=None),
    ],
)
def test_grade_rejects_missing_or_naive_required_trace_fields(trace) -> None:
    with pytest.raises((TypeError, ValueError)):
        proof.grade(_issued_challenge(), "artifact", trace)


def test_grade_allows_optional_identity_and_url_to_be_absent() -> None:
    artifact, behavior = proof.grade(
        _issued_challenge(entity_id=None),
        "artifact",
        _trace(entity_id=None, source_url=None),
    )
    assert artifact.entity_id is None and behavior.entity_id is None
    assert artifact.source_url is None and behavior.source_url is None


def test_grade_rejects_unknown_challenge_and_impossible_completion_time(monkeypatch) -> None:
    with pytest.raises(ValueError, match="issuance receipt"):
        proof.grade(uuid4(), "artifact", _trace())

    challenge_id = _issued_challenge()
    monkeypatch.setattr(proof, "utcnow", lambda: T0)
    with pytest.raises(ValueError, match="window"):
        proof.grade(
            challenge_id,
            "artifact",
            _trace(completed_at=(T0 + timedelta(seconds=1)).isoformat()),
        )


def test_grade_is_replay_idempotent_by_deterministic_event_ids() -> None:
    challenge_id = _issued_challenge()
    first = proof.grade(challenge_id, "artifact", _trace())
    second = proof.grade(challenge_id, "artifact", _trace())
    assert [event.event_id for event in first] == [event.event_id for event in second]

    changed = proof.grade(
        challenge_id,
        "corrected artifact",
        _trace(test_results={"passed": 2, "total": 3, "static_checks_passed": False}),
    )
    assert [event.event_id for event in changed] != [event.event_id for event in first]
