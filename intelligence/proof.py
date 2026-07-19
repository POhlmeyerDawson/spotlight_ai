"""PROOF PROTOCOL — the centerpiece. Owner: C. See C.md H8-12. Protect this block.

generate(): a founder-specific micro-challenge from the deck's central technical claim,
containing one ambiguous requirement (do they ask?) and one planted bad constraint
(do they push back?). The planted constraint is the sharpest signal in the system.

grade(): artifact quality + BEHAVIORAL trace (iteration count, time-to-first-commit,
latency profile, whether they challenged the bad constraint). Behavior is harder to fake.

Results become low-noise observations for A's filter -> the score visibly moves -> the
founder re-enters the gate. That re-entry is the demo.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from core import llm
from intelligence import flags
from schema.events import Challenge, Event, EventKind, Source, utcnow

Judge = Callable[..., str | dict]

FULL_DILIGENCE_CONFIDENCE = 0.99
CAVEAT = "A short proof exercise is informative but is not full diligence."
BEHAVIOR_WEIGHTS = {
    "constraint_pushback": 0.55,
    "iteration": 0.15,
    "time_to_first_commit": 0.10,
    "clarification": 0.15,
    "latency_regularity": 0.05,
}
ARTIFACT_WEIGHTS = {"works": 0.45, "technically_sound": 0.35, "ambiguity_handling": 0.20}

SEEDED_ARTIFACT = """# proof submission

The ambiguous success condition is stated as an explicit assumption. The supplied
constraint was challenged with measurements, then replaced by a safer bounded approach.
The artifact includes a reproducible check and documents its remaining limitation.
"""

_SYSTEM = (
    "Design a 60–90 minute micro-challenge from the supplied claim, for a founder "
    "working in {sector}. It must contain one genuine ambiguity and one subtly "
    "flawed constraint.\n"
    "THE DELIVERABLE IS A REPRODUCIBLE ARTIFACT, NOT NECESSARILY CODE. Choose the "
    "form a competent practitioner in THIS domain would actually produce and that a "
    "stranger could check without taking the founder's word for anything: a runnable "
    "script, a worked calculation with its inputs stated, an assay or test protocol "
    "someone else could run, a reconciliation of a real published dataset, a "
    "regulatory-pathway analysis citing the specific rule it turns on, a teardown or "
    "failure analysis, an annotated design with its rejected alternatives. "
    "REPRODUCIBLE means the reasoning is exposed and checkable, and that is the only "
    "requirement — do not default to a coding exercise unless the claim itself is "
    "about software."
)
# The literal used to be "a 60-90 minute TECHNICAL micro-challenge", which in
# practice meant "write some code". That is a worse failure here than anywhere else
# in this system: the Proof Protocol is the DOCUMENTED MITIGATION for founders with
# no public artifact (data/sources.json coverage_gaps -> "founders whose work is
# closed-source", mitigation: "ISSUE A CHALLENGE rather than infer a low score"), so
# a code-only challenge closed the escape hatch for exactly the people it exists for.
# A founder who cannot be read by the registry AND cannot be read by the challenge is
# simply unscoreable, and would land at the bottom of a min-axis ranking without one
# sentence anywhere explaining why.
_PROOF_SECTOR_FALLBACK = "their stated field"


def _system() -> str:
    """The challenge system prompt, with the active thesis's sector interpolated."""
    from intelligence.screen import _thesis_sector_phrase

    try:
        sector = _thesis_sector_phrase()
    except Exception:  # noqa: BLE001 — a challenge is never worth failing over config
        sector = _PROOF_SECTOR_FALLBACK
    return _SYSTEM.format(sector=sector or _PROOF_SECTOR_FALLBACK)
_PROMPT = (
    "Return JSON with prompt, central_claim, ambiguous_requirement, and "
    "planted_bad_constraint. Each value must be a nonempty string. Do not add facts."
)


def _claim_text(event: Event) -> str:
    value = event.payload.get("claim") or event.evidence_span or ""
    return str(value).strip()


def _fallback(
    company_id: UUID,
    issued_at: datetime,
    central_claim: str = "Claim not available",
    source_claim: Event | None = None,
) -> Challenge:
    challenge = Challenge(
        company_id=company_id,
        prompt="Provide a small reproducible artifact and document the choices you made.",
        central_claim=central_claim,
        ambiguous_requirement="Choose an appropriate success measure and state your assumption.",
        planted_bad_constraint="Use a fixed limit even if measurement shows it is unsuitable.",
        issued_at=issued_at,
    )
    _persist_challenge(challenge, source_claim)
    return challenge


def _persist_challenge(challenge: Challenge, source_claim: Event | None) -> None:
    from memory import store

    store.append(
        Event(
            entity_id=source_claim.entity_id if source_claim is not None else None,
            company_id=challenge.company_id,
            kind=EventKind.PROOF_CHALLENGE_ISSUED,
            source=Source.PROOF_PROTOCOL,
            source_url=source_claim.source_url if source_claim is not None else None,
            observed_at=challenge.issued_at,
            payload={
                "challenge_id": str(challenge.challenge_id),
                "prompt": challenge.prompt,
                "central_claim": challenge.central_claim,
                "ambiguous_requirement": challenge.ambiguous_requirement,
                "planted_bad_constraint": challenge.planted_bad_constraint,
                "source_claim_id": str(source_claim.event_id) if source_claim is not None else None,
            },
            evidence_span=source_claim.evidence_span if source_claim is not None else None,
            confidence=source_claim.confidence if source_claim is not None else 0.5,
        )
    )


def generate(company_id: UUID, judge: Judge | None = None) -> Challenge:
    """Create a claim-specific challenge; deck text is always untrusted data."""
    from memory import store

    issued_at = utcnow()
    judge = judge or llm.complete
    claims = store.events(company_id=company_id, kind=EventKind.DECK_CLAIM, as_of=issued_at)
    claims = [claim for claim in claims if not flags.is_impeached(claim) and _claim_text(claim)]
    if not claims:
        return _fallback(company_id, issued_at)
    claim = max(claims, key=lambda event: (event.confidence, event.observed_at))
    central_claim = _claim_text(claim)
    try:
        raw = judge(
            _PROMPT,
            system=_system(),
            tier="deep",
            untrusted=json.dumps({"central_claim": central_claim}),
            json_mode=True,
        )
        data = raw if isinstance(raw, dict) else json.loads(raw)
        keys = ("prompt", "central_claim", "ambiguous_requirement", "planted_bad_constraint")
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and data[key].strip() for key in keys
        ):
            raise ValueError("malformed challenge")
        challenge = Challenge(
            company_id=company_id,
            prompt=data["prompt"].strip(),
            central_claim=central_claim,
            ambiguous_requirement=data["ambiguous_requirement"].strip(),
            planted_bad_constraint=data["planted_bad_constraint"].strip(),
            issued_at=issued_at,
        )
        _persist_challenge(challenge, claim)
        return challenge
    except Exception:
        return _fallback(company_id, issued_at, central_claim, claim)


def _completed_at(trace: dict) -> datetime:
    value = trace.get("completed_at")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("trace.completed_at is required")
    if parsed.tzinfo is None:
        raise ValueError("trace.completed_at must be timezone-aware")
    return parsed


def _trace_events(
    trace: dict, issued_at: datetime, completed_at: datetime
) -> list[tuple[str, datetime]]:
    raw_events = trace.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("trace.events is required")
    parsed = []
    for item in raw_events:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError("malformed trace event")
        timestamp = _completed_at({"completed_at": item.get("at")})
        if timestamp < issued_at or timestamp > completed_at:
            raise ValueError("trace event is outside the challenge window")
        parsed.append((item["type"], timestamp))
    return sorted(parsed, key=lambda item: item[1])


def _issued_event(challenge_id: UUID, as_of: datetime) -> Event:
    from memory import store

    matches = [
        event
        for event in store.events(kind=EventKind.PROOF_CHALLENGE_ISSUED, as_of=as_of)
        if event.payload.get("challenge_id") == str(challenge_id)
        and event.source == Source.PROOF_PROTOCOL
        and event.company_id is not None
        and not flags.is_impeached(event)
    ]
    if len(matches) != 1:
        raise ValueError("challenge issuance receipt is missing or ambiguous")
    return matches[0]


def _parse_iso(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _clip_score(value: object) -> float | None:
    """A judge's 0..1 sub-score, or None when the judge did not supply a usable one.

    Returns None rather than 0.0 for the missing case. 0.0 is a GRADE — it means the
    judge looked and found the artifact does not work — and handing it back for a
    missing key made an absent, malformed or timed-out reply indistinguishable from a
    damning one. The caller refuses to publish a grade at all when any component is
    None; see the `artifact_graded` branch.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return max(0.0, min(1.0, float(value)))
    return None


def _weighted(components: dict[str, float], weights: dict[str, float]) -> float:
    return round(sum(components[key] * weight for key, weight in weights.items()), 4)


def _iteration_score(count: int) -> float:
    if count == 0:
        return 0.0
    if count == 1:
        return 0.35
    return 0.7 if count <= 3 else 1.0


def _first_commit_score(minutes: float | None) -> float:
    if minutes is None or minutes < 0:
        return 0.5
    if minutes <= 3:
        return 0.4
    if minutes <= 30:
        return 1.0
    if minutes <= 60:
        return 0.7
    return 0.4


def _regularity_score(commit_times: list[datetime]) -> float:
    if len(commit_times) < 3:
        return 0.5
    gaps = [(right - left).total_seconds() for left, right in zip(commit_times, commit_times[1:])]
    if not gaps or max(gaps) <= 0:
        return 0.5
    return min(1.0, sorted(gaps)[len(gaps) // 2] / max(gaps) * 2.0)


def grade(
    challenge_id: UUID,
    artifact: str,
    trace: dict,
    *,
    attestation: dict | None = None,
    expected_company_id: UUID | None = None,
) -> list[Event]:
    """Grade either the raw C trace or D's timestamped commit/question trace adapter."""
    now = utcnow()
    issued = _issued_event(challenge_id, now)
    if expected_company_id is not None and issued.company_id != expected_company_id:
        raise ValueError("challenge does not belong to expected company")
    if trace.get("company_id") and UUID(str(trace["company_id"])) != issued.company_id:
        raise ValueError("trace company does not match challenge")
    if trace.get("entity_id") and UUID(str(trace["entity_id"])) != issued.entity_id:
        raise ValueError("trace entity does not match challenge")
    if issued.company_id is None:
        raise ValueError("issued challenge has no company")

    # D overwrites any client value with its server-built attestation before
    # calling the grader. Keep the keyword form for direct trusted callers.
    attestation = attestation or (
        trace.get("attestation") if isinstance(trace.get("attestation"), dict) else None
    )
    legacy = "commits" in trace and "events" not in trace
    synthesized_attestation = False
    if legacy and attestation is None and trace.get("seeded") is not True:
        synthesized_attestation = True
        attestation = {
            "challenge_anchored": False,
            "attested_fields": [],
            "self_reported_fields": [
                field
                for field in ("pushed_back_on_constraint", "questions_asked", "commits")
                if field in trace
            ],
            "trust": 0.35,
            "demo_seeded": False,
            "note": "Legacy trace was not independently attested.",
        }
    source_url = str(trace["source_url"]) if trace.get("source_url") else None
    if legacy:
        started_at = _parse_iso(trace.get("started_at"))
        submitted_at = _parse_iso(trace.get("submitted_at"))
        seeded = trace.get("seeded") is True
        if (
            started_at is None
            or submitted_at is None
            or submitted_at < started_at
            or submitted_at > now
            or (not seeded and started_at < issued.observed_at)
        ):
            raise ValueError("legacy trace requires ordered aware start/submission times")
        completed_at = submitted_at
        commits = trace.get("commits")
        if not isinstance(commits, list):
            raise ValueError("legacy trace commits must be a list")
        commit_times = []
        for item in commits:
            if not isinstance(item, dict) or (timestamp := _parse_iso(item.get("at"))) is None:
                raise ValueError("legacy trace contains a malformed commit")
            commit_times.append(timestamp)
        commit_times.sort()
        if any(timestamp < started_at or timestamp > submitted_at for timestamp in commit_times):
            raise ValueError("legacy commit is outside the submission window")
        questions = trace.get("questions_asked")
        questions = [question for question in questions or [] if isinstance(question, str)]
        clarified = bool(questions)
        challenged_value = trace.get("pushed_back_on_constraint")
        challenged = challenged_value if isinstance(challenged_value, bool) else None
        if challenged is None:
            try:
                inference = llm.complete(
                    "PLANTED BAD CONSTRAINT:\n"
                    + str(issued.payload.get("planted_bad_constraint") or "unknown"),
                    system="Infer only whether the supplied trace challenged the constraint.",
                    tier="fast",
                    untrusted=json.dumps({"questions": questions, "commits": commits}),
                    json_mode=True,
                )
                challenged = inference.get("pushed_back") if isinstance(inference, dict) else None
            except Exception:
                challenged = None
        try:
            artifact_grade = llm.complete(
                "Grade the submitted proof artifact against the issued challenge. Return JSON with "
                "works, technically_sound, ambiguity_handling, and evidence_span.",
                system="Judge only the submitted work and challenge.",
                tier="deep",
                untrusted=json.dumps(
                    {"challenge": issued.payload, "artifact": artifact, "trace": trace}
                ),
                json_mode=True,
            )
            artifact_grade = artifact_grade if isinstance(artifact_grade, dict) else {}
        except Exception:
            # THE JUDGE NOT ANSWERING IS NOT A FAILING GRADE. This used to be `{}`,
            # which fell straight through _clip_score's 0.0 default into the >0.5
            # thresholds below and published works=False, sound=False,
            # handled_ambiguity=False — a confident three-way zero manufactured from a
            # timeout. `challenged` a few lines up already handles its own judge failure
            # by staying None; this does the same.
            artifact_grade = {}
        graded = {
            "works": _clip_score(artifact_grade.get("works")),
            "technically_sound": _clip_score(artifact_grade.get("technically_sound")),
            "ambiguity_handling": _clip_score(artifact_grade.get("ambiguity_handling")),
        }
        # All three or none. A partial reply is an ungraded artifact, not one that scored
        # zero on whichever fields the judge happened to omit.
        artifact_components = None if any(v is None for v in graded.values()) else graded
        proposed_receipt = artifact_grade.get("evidence_span")
        grounded_receipt = (
            proposed_receipt.strip()
            if isinstance(proposed_receipt, str)
            and proposed_receipt.strip()
            and proposed_receipt.strip() in artifact
            else artifact
        )
        artifact_receipt = grounded_receipt[:240]
        if artifact_components is None:
            handled_ambiguity = works = sound = None
        else:
            handled_ambiguity = artifact_components["ambiguity_handling"] >= 0.5
            works = artifact_components["works"] > 0.5
            sound = artifact_components["technically_sound"] > 0.5
        first_commit = (commit_times[0] - started_at).total_seconds() / 60 if commit_times else None
        behavior_receipt = json.dumps(
            {"questions_asked": questions, "commits": commits}, sort_keys=True
        )
    else:
        completed_at = _completed_at(trace)
        if completed_at < issued.observed_at or completed_at > now:
            raise ValueError("trace completion time is outside the issued challenge window")
        trace_events = _trace_events(trace, issued.observed_at, completed_at)
        test_results = trace.get("test_results")
        if not isinstance(test_results, dict):
            raise ValueError("trace.test_results is required")
        passed, total = test_results.get("passed"), test_results.get("total")
        valid_counts = (
            all(isinstance(value, int) and not isinstance(value, bool) for value in (passed, total))
            and 0 <= passed <= total
        )
        works = bool(valid_counts and total > 0 and passed == total)
        sound = bool(valid_counts and test_results.get("static_checks_passed") is True)
        event_types = [event_type for event_type, _ in trace_events]
        challenged = "constraint_challenged" in event_types
        clarified = "clarifying_question" in event_types
        handled_ambiguity = clarified or "assumption_stated" in event_types
        commit_times = [
            timestamp for event_type, timestamp in trace_events if event_type == "commit"
        ]
        first_commit = (
            (commit_times[0] - issued.observed_at).total_seconds() / 60 if commit_times else None
        )
        artifact_components = {
            "works": float(works),
            "technically_sound": float(sound),
            "ambiguity_handling": float(handled_ambiguity),
        }
        artifact_receipt = artifact[:240] or "Artifact submitted without text"
        behavior_receipt = json.dumps(trace.get("events"), sort_keys=True)

    iterations = len(commit_times)
    latency_profile = [
        (right - left).total_seconds() / 60 for left, right in zip(commit_times, commit_times[1:])
    ]
    score_challenged = challenged
    if attestation:
        attested_fields = set(attestation.get("attested_fields") or [])
        self_reported_fields = set(attestation.get("self_reported_fields") or [])
        if (
            "pushed_back_on_constraint" in self_reported_fields
            and "pushed_back_on_constraint" not in attested_fields
        ):
            score_challenged = None
    behavior_components = {
        "constraint_pushback": (
            1.0 if score_challenged is True else 0.15 if score_challenged is False else 0.5
        ),
        "iteration": _iteration_score(iterations),
        "time_to_first_commit": _first_commit_score(first_commit),
        "clarification": 1.0 if clarified else 0.0,
        "latency_regularity": _regularity_score(commit_times),
    }
    # None when the judge never returned a usable grade. `_weighted` over a stand-in
    # would emit a number, and `value`/`y` are what the founder score consumes — an
    # ungraded artifact must not reach it as a low one.
    artifact_value = (
        None if artifact_components is None else _weighted(artifact_components, ARTIFACT_WEIGHTS)
    )
    behavior_value = _weighted(behavior_components, BEHAVIOR_WEIGHTS)
    submission_digest = hashlib.sha256(
        json.dumps(
            {"artifact": artifact, "trace": trace, "attestation": attestation},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()

    seeded = trace.get("seeded") is True
    disclosure = str(trace.get("disclosure") or "") if seeded else None
    # D owns confidence scaling for server-built attestations. C only applies a
    # low-trust fallback when the legacy adapter arrived without D provenance.
    attestation_trust = 0.35 if synthesized_attestation else 1.0
    artifact_confidence = round(0.9 * attestation_trust, 3)
    behavior_confidence = round(0.95 * attestation_trust, 3)
    integrity_flags = []
    unattested_fields = (
        set(attestation.get("self_reported_fields") or [])
        - set(attestation.get("attested_fields") or [])
        if attestation
        else set()
    )
    if unattested_fields:
        integrity_flags.append("unattested_trace")
    if artifact_components is None:
        # Say so on the record rather than letting a null value be read as a shrug.
        artifact_confidence = 0.0
        integrity_flags.append("artifact_ungraded")
    if seeded:
        artifact_confidence = 0.0
        behavior_confidence = 0.0
        integrity_flags.append("seeded_demo")
    artifact_event = Event(
        event_id=uuid5(NAMESPACE_URL, f"proof-artifact:{challenge_id}:{submission_digest}"),
        entity_id=issued.entity_id,
        company_id=issued.company_id,
        kind=EventKind.PROOF_ARTIFACT,
        source=Source.PROOF_PROTOCOL,
        source_url=source_url,
        observed_at=completed_at,
        payload={
            "challenge_id": str(challenge_id),
            "artifact": artifact,
            "works": works,
            "sound": sound,
            "handled_ambiguity": handled_ambiguity,
            "value": None if seeded else artifact_value,
            "y": None if seeded else artifact_value,
            "components": artifact_components,
            "confidence": artifact_confidence,
            "caveat": CAVEAT,
            "seeded": seeded,
            "disclosure": disclosure,
            "attestation": attestation,
        },
        evidence_span=artifact_receipt,
        confidence=artifact_confidence,
        integrity_flags=integrity_flags,
    )
    behavior_event = Event(
        event_id=uuid5(NAMESPACE_URL, f"proof-behavior:{challenge_id}:{submission_digest}"),
        entity_id=issued.entity_id,
        company_id=issued.company_id,
        kind=EventKind.PROOF_BEHAVIOR,
        source=Source.PROOF_PROTOCOL,
        source_url=source_url,
        observed_at=completed_at,
        payload={
            "challenge_id": str(challenge_id),
            "challenged_bad_constraint": challenged is True,
            "pushed_back_on_constraint": challenged,
            "asked_clarifying": clarified,
            "iteration_count": iterations,
            "time_to_first_commit_min": first_commit,
            "latency_profile": latency_profile,
            "value": None if seeded else behavior_value,
            "y": None if seeded else behavior_value,
            "components": behavior_components,
            "confidence": behavior_confidence,
            "caveat": CAVEAT,
            "seeded": seeded,
            "disclosure": disclosure,
            "attestation": attestation,
        },
        evidence_span=behavior_receipt[:240],
        confidence=behavior_confidence,
        integrity_flags=integrity_flags,
    )
    return [artifact_event, behavior_event]


def seed_demo_completion(company_id: UUID) -> list[Event]:
    """Pre-run completion adapter used by D's demo grade route; disclosed, never presented live."""
    from memory import store

    now = utcnow()
    issued = store.events(as_of=now, company_id=company_id, kind=EventKind.PROOF_CHALLENGE_ISSUED)
    if not issued:
        raise ValueError("no issued proof challenge for demo completion")
    challenge_id = UUID(str(issued[-1].payload["challenge_id"]))
    # Seeded activity is explicitly disclosed and anchored in the past so the
    # demo never emits future-dated evidence, even when issuance happened now.
    start = now - timedelta(minutes=80)

    def at(minutes: int) -> str:
        return (start + timedelta(minutes=minutes)).isoformat()

    trace = {
        "started_at": start.isoformat(),
        "submitted_at": at(80),
        "questions_asked": [
            "Which interpretation of the success condition should the measurement use?"
        ],
        "pushed_back_on_constraint": True,
        "commits": [
            {"at": at(15), "message": "add measurement harness", "files": 4},
            {"at": at(35), "message": "implement baseline", "files": 3},
            {"at": at(58), "message": "replace unsuitable constraint", "files": 5},
            {"at": at(76), "message": "document assumption and checks", "files": 2},
        ],
        "seeded": True,
        "disclosure": "Generator and grader are live; this completion is pre-run.",
    }
    return grade(challenge_id, SEEDED_ARTIFACT, trace)
