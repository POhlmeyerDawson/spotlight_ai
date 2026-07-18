"""Proof Protocol. Owner: C. The LLM is always mocked — no live calls in CI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from core import llm
from intelligence import proof
from memory import db, store
from schema.events import Event, EventKind, Source

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=365)

GENERATED = {
    "central_claim": "Sub-millisecond p99 reads at 10k rps on a single core.",
    "prompt": "Build a read-through cache in front of a slow store. Results must be fresh. "
    "Put a global lock around the read path so consistency is guaranteed.",
    "ambiguous_requirement": "'Results must be fresh' is undefined: wall-clock staleness "
    "or writes-since-read. Both readings are defensible.",
    "planted_bad_constraint": "The required global lock on the read path serialises 95% "
    "of traffic onto one core for a guarantee this workload never needs.",
}

ARTIFACT_GRADE = {
    "works": 0.8,
    "technically_sound": 0.75,
    "ambiguity_handling": 0.9,
    "evidence_span": "I implemented wall-clock (60s TTL) ... flagging rather than guessing.",
    "notes": "Real, benchmarked, assumption stated.",
}


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    yield
    db.reset_connections()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Route every llm.complete by prompt shape. A live call would blow up here."""

    def fake(prompt, *, system=None, tier="fast", untrusted=None, json_mode=False, **kw):
        if prompt.startswith("Below are the technical claims"):
            return dict(GENERATED)
        if "PLANTED BAD CONSTRAINT:" in prompt:
            return {"pushed_back": True, "evidence": "inferred from the commit log"}
        return dict(ARTIFACT_GRADE)

    monkeypatch.setattr(llm, "complete", fake)


def _company_with_claim() -> tuple:
    company_id, entity_id = uuid4(), uuid4()
    store.append(
        Event(
            company_id=company_id,
            entity_id=entity_id,
            kind=EventKind.DECK_CLAIM,
            source=Source.DECK,
            observed_at=T0,
            payload={"claim": "We serve sub-millisecond p99 reads at 10k rps on one core."},
            evidence_span="slide 6",
        )
    )
    return company_id, entity_id


def _trace(*, pushed_back: bool | None) -> dict:
    start = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)

    def at(m: int) -> str:
        return (start + timedelta(minutes=m)).isoformat()

    return {
        "started_at": start.isoformat(),
        "submitted_at": at(80),
        "questions_asked": ["Is 'fresh' wall-clock staleness or writes-since-read?"],
        "pushed_back_on_constraint": pushed_back,
        "commits": [
            {"at": at(15), "message": "scaffold + bench", "files": 4},
            {"at": at(38), "message": "naive impl", "files": 3},
            {"at": at(60), "message": "swap the lock strategy", "files": 6},
            {"at": at(78), "message": "document assumptions", "files": 2},
        ],
    }


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_plants_all_three_fields() -> None:
    company_id, _ = _company_with_claim()
    challenge = proof.generate(company_id)

    # All three are rendered by D as "here is what we planted, and why" — the reveal.
    assert challenge.central_claim.strip()
    assert challenge.ambiguous_requirement.strip()
    assert challenge.planted_bad_constraint.strip()
    assert challenge.prompt.strip()
    assert challenge.company_id == company_id


def test_generate_emits_issued_event_carrying_the_planted_elements() -> None:
    company_id, entity_id = _company_with_claim()
    challenge = proof.generate(company_id)

    issued = store.events(
        as_of=LATER, company_id=company_id, kind=EventKind.PROOF_CHALLENGE_ISSUED
    )
    assert len(issued) == 1
    ev = issued[0]
    assert ev.payload["challenge_id"] == str(challenge.challenge_id)
    assert ev.payload["planted_bad_constraint"] == challenge.planted_bad_constraint
    assert ev.payload["ambiguous_requirement"] == challenge.ambiguous_requirement
    assert ev.source == Source.PROOF_PROTOCOL
    # Must hang off the founder entity or A's filter never sees the result.
    assert ev.entity_id == entity_id


def test_generate_sends_deck_text_as_untrusted_never_concatenated(monkeypatch) -> None:
    company_id, _ = _company_with_claim()
    seen: dict = {}

    def fake(prompt, *, system=None, tier="fast", untrusted=None, json_mode=False, **kw):
        seen.update(prompt=prompt, untrusted=untrusted, tier=tier)
        return dict(GENERATED)

    monkeypatch.setattr(llm, "complete", fake)
    proof.generate(company_id)

    assert "sub-millisecond p99" in (seen["untrusted"] or "")
    assert "sub-millisecond p99" not in seen["prompt"]  # Invariant #4
    assert seen["tier"] == "deep"


# ---------------------------------------------------------------------------
# grade
# ---------------------------------------------------------------------------


def _graded(pushed_back: bool | None) -> dict[EventKind, Event]:
    company_id, _ = _company_with_claim()
    challenge = proof.generate(company_id)
    events = proof.grade(challenge.challenge_id, proof.SEEDED_ARTIFACT, _trace(pushed_back=pushed_back))
    return {ev.kind: ev for ev in events}


def test_grade_emits_both_events_with_the_score_payload_triple() -> None:
    by_kind = _graded(True)
    assert set(by_kind) == {EventKind.PROOF_ARTIFACT, EventKind.PROOF_BEHAVIOR}

    for ev in by_kind.values():
        # The contract with memory/score.py: value and y must both be present and agree.
        assert "value" in ev.payload and "y" in ev.payload and "components" in ev.payload
        assert ev.payload["value"] == ev.payload["y"]
        assert 0.0 <= ev.payload["value"] <= 1.0
        assert isinstance(ev.payload["components"], dict) and ev.payload["components"]
        assert ev.evidence_span  # a score without a receipt is not a score
        assert ev.source == Source.PROOF_PROTOCOL


def test_pushing_back_on_the_planted_constraint_scores_higher_than_complying() -> None:
    """THE core assertion. Two founders, identical artifact and identical trace; the only
    difference is whether they challenged the bad constraint. That must dominate."""
    pushed = _graded(True)[EventKind.PROOF_BEHAVIOR].payload
    complied = _graded(False)[EventKind.PROOF_BEHAVIOR].payload

    assert pushed["value"] > complied["value"]
    assert pushed["components"]["constraint_pushback"] > complied["components"]["constraint_pushback"]
    # Every other behavioural component is identical, so the gap is attributable.
    others = set(proof.BEHAVIOR_WEIGHTS) - {"constraint_pushback"}
    assert all(pushed["components"][k] == complied["components"][k] for k in others)
    assert pushed["value"] - complied["value"] > 0.3  # dominant, not a tiebreaker
    assert pushed["pushed_back_on_constraint"] is True
    assert complied["pushed_back_on_constraint"] is False


def test_unknown_pushback_lands_between_and_is_inferred_when_absent() -> None:
    company_id, _ = _company_with_claim()
    challenge = proof.generate(company_id)
    trace = _trace(pushed_back=None)
    events = proof.grade(challenge.challenge_id, "x", trace)
    behavior = next(e for e in events if e.kind == EventKind.PROOF_BEHAVIOR)
    # The mocked inference says True, so an absent flag must be inferred, not defaulted.
    assert behavior.payload["pushed_back_on_constraint"] is True


def test_proof_confidence_never_reads_as_full_diligence() -> None:
    for ev in _graded(True).values():
        assert ev.confidence < proof.FULL_DILIGENCE_CONFIDENCE
        assert ev.payload["confidence"] < proof.FULL_DILIGENCE_CONFIDENCE
        assert ev.payload["caveat"]  # D renders this next to the moved score


def test_grade_rejects_an_unknown_challenge_id() -> None:
    with pytest.raises(ValueError, match="no PROOF_CHALLENGE_ISSUED"):
        proof.grade(uuid4(), "artifact", _trace(pushed_back=True))


def test_graded_events_are_readable_as_observations_by_the_founder_score() -> None:
    """The contract that makes the demo work: proof results re-enter A's filter."""
    from memory import score

    company_id, entity_id = _company_with_claim()
    challenge = proof.generate(company_id)
    events = proof.grade(challenge.challenge_id, proof.SEEDED_ARTIFACT, _trace(pushed_back=True))

    kept = {o.event_id for o in score.observations(entity_id, as_of=LATER).kept}
    assert {ev.event_id for ev in events} <= kept


# ---------------------------------------------------------------------------
# Type 2 demo seed
# ---------------------------------------------------------------------------


def test_seed_demo_completion_is_interesting_and_discloses_itself() -> None:
    company_id, _ = _company_with_claim()
    challenge = proof.generate(company_id)
    seed = proof.seed_demo_completion(company_id)

    assert seed["seeded"] is True and seed["disclosure"]
    assert seed["challenge_id"] == str(challenge.challenge_id)
    trace = seed["trace"]
    assert trace["pushed_back_on_constraint"] is True
    assert len(trace["questions_asked"]) >= 1
    assert len(trace["commits"]) >= 3

    events = proof.grade(challenge.challenge_id, seed["artifact"], trace)
    behavior = next(e for e in events if e.kind == EventKind.PROOF_BEHAVIOR)
    assert behavior.payload["value"] > 0.7  # a strong run, but still not full diligence
