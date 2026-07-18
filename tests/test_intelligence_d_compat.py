"""RED-first compatibility contract between intelligence outputs and D consumers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from intelligence import flags, proof, validator
from schema.events import Event, EventKind, Source

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
COMPANY_ID = uuid4()
ENTITY_ID = uuid4()
CHALLENGE_ID = uuid4()


def _signal(*, observed_at: datetime = T0) -> Event:
    return Event(
        entity_id=ENTITY_ID,
        company_id=COMPANY_ID,
        kind=EventKind.RELEASE,
        source=Source.GITHUB,
        observed_at=observed_at,
        payload={"version": "0.2.0", "repo": "sample/project"},
        evidence_span="release 0.2.0",
        confidence=0.8,
    )


def _issued() -> Event:
    return Event(
        entity_id=ENTITY_ID,
        company_id=COMPANY_ID,
        kind=EventKind.PROOF_CHALLENGE_ISSUED,
        source=Source.PROOF_PROTOCOL,
        observed_at=T0,
        payload={"challenge_id": str(CHALLENGE_ID)},
        evidence_span="issued challenge",
    )


def _legacy_trace(*, pushed_back: bool = True) -> dict:
    started = T0 + timedelta(minutes=5)

    def at(minutes: int) -> str:
        return (started + timedelta(minutes=minutes)).isoformat()

    return {
        "started_at": started.isoformat(),
        "submitted_at": at(80),
        "questions_asked": ["Which workload should the measurement represent?"],
        "pushed_back_on_constraint": pushed_back,
        "commits": [
            {"at": at(15), "message": "initial measurement", "files": 3},
            {"at": at(35), "message": "first implementation", "files": 4},
            {"at": at(60), "message": "revise approach", "files": 5},
            {"at": at(75), "message": "document assumptions", "files": 2},
        ],
    }


def _install_issued_store(monkeypatch) -> None:
    cutoff = T0 + timedelta(days=1)
    monkeypatch.setattr(proof, "utcnow", lambda: cutoff)
    monkeypatch.setattr("memory.store.events", lambda **kwargs: [_issued()])


def _d_derive_y(payload: dict) -> float | None:
    """Small local clone of D's scalar payload reader."""
    for key in ("value", "y", "yes_rate", "score"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(1.0, float(value)))
    rows = payload.get("flags")
    if isinstance(rows, list) and rows:
        numerator = denominator = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            weight = row.get("weight", 1.0)
            weight = (
                float(weight)
                if isinstance(weight, (int, float)) and not isinstance(weight, bool)
                else 1.0
            )
            denominator += weight
            if bool(row.get("fired")):
                numerator += weight
        return numerator / denominator if denominator > 0 else None
    return None


def test_rule_id_alias_is_exact_for_every_rule() -> None:
    assert flags.RULES
    assert all(rule.id == rule.rule_id for rule in flags.RULES)


def test_code_kind_vocabulary_is_exported_for_d_gate() -> None:
    assert flags.CODE_KINDS
    assert EventKind.PROOF_ARTIFACT in flags.CODE_KINDS
    assert set(flags.CODE_KINDS) <= set(flags.ARTIFACT_KINDS)


def test_evaluate_keeps_per_rule_contract_and_appends_d_rollup() -> None:
    as_of = T0 + timedelta(days=2)
    signal = _signal()
    result = flags.evaluate(ENTITY_ID, as_of, events=[signal])
    per_rule, rollup = result[:-1], result[-1]

    direct = flags.evaluate_events([signal], entity_id=ENTITY_ID, as_of=as_of)
    assert [(event.payload, event.observed_at) for event in per_rule] == [
        (event.payload, event.observed_at) for event in direct
    ]
    assert set(rollup.payload) == {
        "value",
        "y",
        "flags",
        "rules_fired",
        "rollup",
        "self_consistency",
        "observation_role",
        "derived_from_event_ids",
        "source_evidence_event_ids",
    }
    expected_y = flags.observation(per_rule)[0]
    assert rollup.payload["value"] == expected_y
    assert rollup.payload["y"] == expected_y
    assert rollup.payload["rollup"] is True
    assert rollup.payload["observation_role"] == "rollup"
    assert rollup.payload["derived_from_event_ids"] == [str(event.event_id) for event in per_rule]
    assert rollup.payload["source_evidence_event_ids"] == [str(signal.event_id)]
    assert rollup.observed_at == T0
    assert rollup.observed_at <= as_of
    assert rollup.evidence_span

    rows = rollup.payload["flags"]
    assert len(rows) == len(flags.RULES)
    assert [row["id"] for row in rows] == [rule.id for rule in flags.RULES]
    assert all(set(row) == {"id", "fired", "weight", "applicable"} for row in rows)
    by_id = {event.payload["rule_id"]: event for event in per_rule}
    for rule, row in zip(flags.RULES, rows):
        assert row["weight"] == rule.weight
        assert row["applicable"] is (rule.id in by_id)
        assert row["fired"] is (
            bool(by_id[rule.id].payload["fired"]) if rule.id in by_id else False
        )
    assert rollup.payload["rules_fired"] == [
        row["id"] for row in rows if row["applicable"] and row["fired"]
    ]


def test_evaluate_store_wrapper_uses_caller_cutoff(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_events(**kwargs):
        calls.append(kwargs)
        return [_signal()]

    monkeypatch.setattr("memory.store.events", fake_events)
    flags.evaluate(ENTITY_ID, T0 + timedelta(days=1))
    assert calls == [{"entity_id": ENTITY_ID, "as_of": T0 + timedelta(days=1)}]


def test_validator_check_claims_uses_supplied_cutoff_without_calling_utcnow(monkeypatch) -> None:
    cutoff = T0 + timedelta(days=3)
    calls: list[dict] = []

    def fake_events(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr(
        validator.web_search,
        "search",
        lambda query: (_ for _ in ()).throw(
            AssertionError("historical validation must not search")
        ),
    )
    monkeypatch.setattr(
        validator,
        "utcnow",
        lambda: (_ for _ in ()).throw(AssertionError("utcnow must not run")),
    )
    assert validator.check_claims(COMPANY_ID, as_of=cutoff) == []
    assert calls == [{"company_id": COMPANY_ID, "kind": EventKind.DECK_CLAIM, "as_of": cutoff}]


def test_validator_check_claims_defaults_to_one_utcnow_cutoff(monkeypatch) -> None:
    cutoff = T0 + timedelta(days=4)
    calls: list[dict] = []
    now_calls = 0

    def fake_now():
        nonlocal now_calls
        now_calls += 1
        return cutoff

    def fake_events(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("memory.store.events", fake_events)
    monkeypatch.setattr(validator, "utcnow", fake_now)
    assert validator.check_claims(COMPANY_ID) == []
    assert now_calls == 1
    assert calls[0]["as_of"] == cutoff


def test_behavior_weights_exist_and_include_dominant_component() -> None:
    assert isinstance(proof.BEHAVIOR_WEIGHTS, dict) and proof.BEHAVIOR_WEIGHTS
    assert "constraint_pushback" in proof.BEHAVIOR_WEIGHTS


def test_full_diligence_confidence_exists_and_is_bounded() -> None:
    assert 0.0 < proof.FULL_DILIGENCE_CONFIDENCE <= 1.0


def test_seeded_artifact_exists_and_is_nonempty() -> None:
    assert isinstance(proof.SEEDED_ARTIFACT, str) and proof.SEEDED_ARTIFACT.strip()


def test_grade_legacy_trace_emits_scalar_payloads_readable_by_d(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    events = proof.grade(CHALLENGE_ID, "working artifact", _legacy_trace())

    assert [event.kind for event in events] == [EventKind.PROOF_ARTIFACT, EventKind.PROOF_BEHAVIOR]
    artifact, behavior = events
    assert {
        "challenge_id",
        "artifact",
        "works",
        "sound",
        "handled_ambiguity",
    } <= set(artifact.payload)
    assert {
        "challenge_id",
        "challenged_bad_constraint",
        "asked_clarifying",
        "iteration_count",
        "time_to_first_commit_min",
        "latency_profile",
    } <= set(behavior.payload)
    for event in events:
        assert {"value", "y", "components", "confidence", "caveat"} <= set(event.payload)
        assert event.payload["value"] == event.payload["y"]
        assert 0.0 <= event.payload["value"] <= 1.0
        assert _d_derive_y(event.payload) == event.payload["value"]
        assert isinstance(event.payload["components"], dict) and event.payload["components"]
        assert event.payload["confidence"] < proof.FULL_DILIGENCE_CONFIDENCE
        assert event.payload["caveat"]
    assert behavior.payload["iteration_count"] == 4
    assert behavior.payload["time_to_first_commit_min"] == pytest.approx(15.0)
    assert behavior.payload["asked_clarifying"] is True
    assert behavior.payload["challenged_bad_constraint"] is True


def test_constraint_pushback_dominates_behavior_value(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    attestation = {
        "attested_fields": ["pushed_back_on_constraint"],
        "self_reported_fields": [],
        "trust": 1.0,
    }
    pushed = proof.grade(
        CHALLENGE_ID,
        "same artifact",
        _legacy_trace(pushed_back=True),
        attestation=attestation,
    )[1].payload
    complied = proof.grade(
        CHALLENGE_ID,
        "same artifact",
        _legacy_trace(pushed_back=False),
        attestation=attestation,
    )[1].payload

    assert pushed["value"] - complied["value"] > 0.3
    assert (
        pushed["components"]["constraint_pushback"] > complied["components"]["constraint_pushback"]
    )
    other_components = set(proof.BEHAVIOR_WEIGHTS) - {"constraint_pushback"}
    assert all(pushed["components"][key] == complied["components"][key] for key in other_components)


def test_seed_demo_completion_returns_events_consumable_by_d_route(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    events = proof.seed_demo_completion(COMPANY_ID)

    assert isinstance(events, list)
    assert [event.kind for event in events] == [EventKind.PROOF_ARTIFACT, EventKind.PROOF_BEHAVIOR]
    assert all(isinstance(event, Event) for event in events)
    assert all(event.company_id == COMPANY_ID for event in events)
    assert all(event.payload["caveat"] for event in events)
    assert all(event.payload["seeded"] is True for event in events)
    assert all(event.payload["disclosure"] for event in events)
    assert all(event.observed_at <= T0 + timedelta(days=1) for event in events)


def test_legacy_grade_rejects_commit_outside_submission_window(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    trace = _legacy_trace()
    trace["commits"][0]["at"] = (T0 - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="outside the submission window"):
        proof.grade(CHALLENGE_ID, "artifact", trace)


def test_legacy_grade_rejects_malformed_commit(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    trace = _legacy_trace()
    trace["commits"].append({"message": "missing timestamp"})
    with pytest.raises(ValueError, match="malformed commit"):
        proof.grade(CHALLENGE_ID, "artifact", trace)


def test_artifact_receipt_must_be_grounded_in_submission(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    monkeypatch.setattr(
        proof.llm,
        "complete",
        lambda *args, **kwargs: {
            "works": 1,
            "technically_sound": 1,
            "ambiguity_handling": 1,
            "evidence_span": "hallucinated receipt",
        },
    )
    artifact = proof.grade(CHALLENGE_ID, "actual submitted evidence", _legacy_trace())[0]
    assert artifact.evidence_span == "actual submitted evidence"


def test_missing_artifact_judgment_does_not_pass_submission(monkeypatch) -> None:
    _install_issued_store(monkeypatch)
    monkeypatch.setattr(proof.llm, "complete", lambda *args, **kwargs: {})
    artifact = proof.grade(CHALLENGE_ID, "unverified artifact", _legacy_trace())[0]
    assert artifact.payload["works"] is False
    assert artifact.payload["sound"] is False
    assert artifact.payload["value"] == 0.0


def test_mixed_company_history_does_not_emit_cross_company_rollup() -> None:
    other = _signal()
    other.company_id = uuid4()
    result = flags.evaluate(ENTITY_ID, T0 + timedelta(days=1), events=[_signal(), other])
    assert all(event.payload.get("rollup") is not True for event in result)
