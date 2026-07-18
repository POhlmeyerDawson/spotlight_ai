"""PROOF PROTOCOL — the centerpiece. Owner: C. See C.md H8-12. Protect this block.

generate(): a founder-specific micro-challenge from the deck's central technical claim,
containing one ambiguous requirement (do they ask?) and one planted bad constraint
(do they push back?). The planted constraint is the sharpest signal in the system.

grade(): artifact quality + BEHAVIORAL trace (iteration count, time-to-first-commit,
latency profile, whether they challenged the bad constraint). Behavior is harder to fake.

Results become low-noise observations for A's filter -> the score visibly moves -> the
founder re-enters the gate. That re-entry is the demo.

Payload contract with memory/score.py: every emitted event carries {value, y, components}
with value == y, so the two implementations cannot drift apart.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from uuid import UUID

from core import llm
from memory import queries, store
from schema.events import Challenge, Event, EventKind, Source, utcnow

# A Proof Protocol result is 60-90 minutes of evidence. It is never full diligence, and
# the emitted confidence has to say so out loud — D renders this next to the moved score.
PROOF_CONFIDENCE = 0.55
FULL_DILIGENCE_CONFIDENCE = 0.80
CAVEAT = (
    "Proof Protocol result: a single 60-90 minute exercise, not full diligence. "
    "The interval around this reading stays wide and must be displayed as such."
)

# Behaviour weights. Pushing back on the planted bad constraint dominates on purpose —
# it is the one signal a founder cannot produce by polishing the artifact afterwards.
BEHAVIOR_WEIGHTS = {
    "constraint_pushback": 0.50,
    "iteration": 0.18,
    "time_to_first_commit": 0.12,
    "clarification": 0.12,
    "latency_regularity": 0.08,
}
PUSHBACK_SCORE = {True: 1.0, False: 0.15, None: 0.5}  # None = we could not tell; don't punish

ARTIFACT_WEIGHTS = {"works": 0.40, "technically_sound": 0.30, "ambiguity_handling": 0.30}

# --- prompts (module constants so they are tunable without hunting through code) -------

SYSTEM = (
    "You design and grade short technical exercises for early-stage founders. "
    "You judge only the work in front of you: what was built, and how the person behaved "
    "while building it. Never consider or mention where anyone studied, who they have "
    "worked for, or who funded them — those facts are irrelevant here and forbidden. "
    "Respond with JSON only."
)

GENERATE_PROMPT = """Below are the technical claims a founder made in their deck.

Design ONE micro-challenge, sized at 60-90 minutes of real work, that directly tests the
single most load-bearing technical claim. It must be buildable from scratch, with no
access to their private code or data.

The challenge MUST contain exactly two planted elements, woven into the prose so they
read as an ordinary spec. Never label them.

1. ONE AMBIGUOUS REQUIREMENT - deliberately underspecified, with at least two defensible
   readings. We are watching whether they ask a clarifying question, or assume and state
   the assumption. Both are good. Silently guessing is not.

2. ONE PLANTED BAD CONSTRAINT - a specific instruction that is subtly wrong or
   counterproductive for this problem. It should look authoritative, but a competent
   engineer working on this exact claim should notice it hurts. Wrong on the merits, not
   merely unusual, and not so glaring that it reads as a joke. We are watching whether
   they push back, or comply.

Return JSON:
{
  "central_claim": "the deck claim this tests, quoted or tightly paraphrased",
  "prompt": "the full challenge text handed to the founder, with both planted elements
             in-line and unlabelled",
  "ambiguous_requirement": "the exact underspecified requirement, and the two defensible
                            readings of it",
  "planted_bad_constraint": "the exact bad constraint, why it is wrong on the merits, and
                             what a founder who pushes back should say"
}"""

INFER_PUSHBACK_PROMPT = """A founder was given a challenge containing a planted bad
constraint they were never told about.

PLANTED BAD CONSTRAINT:
{constraint}

Below are the questions they asked and their commit messages. Decide whether they
challenged, questioned, or deliberately worked around THAT SPECIFIC constraint — as
opposed to complying with it, or raising some unrelated point.

Return JSON: {{"pushed_back": true|false|null, "evidence": "the exact question or commit
message showing it, quoted; empty string if none"}}
Use null only when the record is genuinely too thin to tell."""

GRADE_ARTIFACT_PROMPT = """Grade the artifact submitted for the challenge below.

CHALLENGE:
{prompt}

THE AMBIGUOUS REQUIREMENT WE PLANTED:
{ambiguous}

Score each 0.0-1.0, strictly. 0.5 is a mediocre but real attempt; reserve anything above
0.85 for work that would survive review by someone who knows this domain.

Return JSON:
{{
  "works": <does it actually run and do the thing>,
  "technically_sound": <is the approach correct, are the failure modes handled>,
  "ambiguity_handling": <did they resolve the underspecified requirement well and state
                         the assumption, or did they silently guess>,
  "evidence_span": "one exact quote from the artifact that justifies these scores",
  "notes": "two sentences maximum"
}}"""


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def generate(company_id: UUID) -> Challenge:
    as_of = utcnow()
    claim_text = _claim_digest(company_id, as_of)

    out = _as_dict(
        llm.complete(
            GENERATE_PROMPT, system=SYSTEM, tier="deep", untrusted=claim_text, json_mode=True
        )
    )

    challenge = Challenge(
        company_id=company_id,
        prompt=_text(out, "prompt"),
        central_claim=_text(out, "central_claim") or claim_text[:400],
        ambiguous_requirement=_text(out, "ambiguous_requirement"),
        planted_bad_constraint=_text(out, "planted_bad_constraint"),
    )

    store.append(
        Event(
            company_id=company_id,
            entity_id=_founder_entity_id(company_id, as_of),
            kind=EventKind.PROOF_CHALLENGE_ISSUED,
            source=Source.PROOF_PROTOCOL,
            observed_at=challenge.issued_at,
            payload={
                "challenge_id": str(challenge.challenge_id),
                "prompt": challenge.prompt,
                "central_claim": challenge.central_claim,
                # Stored so D can render "here is what we planted, and why" — the reveal.
                "ambiguous_requirement": challenge.ambiguous_requirement,
                "planted_bad_constraint": challenge.planted_bad_constraint,
            },
            evidence_span=challenge.central_claim,
            confidence=PROOF_CONFIDENCE,
        )
    )
    return challenge


def _claim_digest(company_id: UUID, as_of: datetime) -> str:
    """Founder-supplied deck text. Reaches the LLM via untrusted=, never concatenated."""
    lines = []
    for ev in queries.claims(company_id, as_of):
        p = ev.payload if isinstance(ev.payload, dict) else {}
        text = p.get("claim") or p.get("text") or p.get("statement") or ev.evidence_span
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else "- (no deck claims on record)"


def _founder_entity_id(company_id: UUID, as_of: datetime) -> UUID | None:
    """Proof events must hang off the founder entity, or A's filter never sees them."""
    ids = [ev.entity_id for ev in store.events(as_of=as_of, company_id=company_id) if ev.entity_id]
    return max(set(ids), key=ids.count) if ids else None


# ---------------------------------------------------------------------------
# grade
# ---------------------------------------------------------------------------


def grade(challenge_id: UUID, artifact: str, trace: dict) -> list[Event]:
    as_of = utcnow()
    issued = _load_challenge_event(challenge_id, as_of)
    planted = issued.payload

    pushed_back, evidence = _resolve_pushback(trace, planted.get("planted_bad_constraint"))

    art_value, art_components, art_span = _grade_artifact(artifact, planted)
    beh_value, beh_components, beh_span = _grade_behavior(trace, pushed_back, evidence)

    common = {
        "company_id": issued.company_id,
        "entity_id": issued.entity_id,
        "source": Source.PROOF_PROTOCOL,
        "observed_at": _parse_iso(trace.get("submitted_at")) or as_of,
        "confidence": PROOF_CONFIDENCE,
    }

    events = [
        Event(
            kind=EventKind.PROOF_ARTIFACT,
            payload=_payload(challenge_id, art_value, art_components),
            evidence_span=art_span,
            **common,
        ),
        Event(
            kind=EventKind.PROOF_BEHAVIOR,
            payload=_payload(
                challenge_id,
                beh_value,
                beh_components,
                pushed_back_on_constraint=pushed_back,
            ),
            evidence_span=beh_span,
            **common,
        ),
    ]
    for ev in events:
        store.append(ev)
    return events


def _payload(challenge_id: UUID, value: float, components: dict, **extra) -> dict:
    """The {value, y, components} triple is the contract with memory/score.py: it reads
    these as low-noise observations, and both keys must always agree."""
    return {
        "value": value,
        "y": value,
        "components": components,
        "challenge_id": str(challenge_id),
        "confidence": PROOF_CONFIDENCE,
        "caveat": CAVEAT,
        **extra,
    }


def _load_challenge_event(challenge_id: UUID, as_of: datetime) -> Event:
    for ev in store.events(as_of=as_of, kind=EventKind.PROOF_CHALLENGE_ISSUED):
        if ev.payload.get("challenge_id") == str(challenge_id):
            return ev
    raise ValueError(f"no PROOF_CHALLENGE_ISSUED event for challenge_id {challenge_id}")


def _resolve_pushback(trace: dict, constraint: str | None) -> tuple[bool | None, str]:
    """Explicit beats inferred — only ask the LLM when the trace does not say."""
    stated = trace.get("pushed_back_on_constraint")
    if isinstance(stated, bool):
        return stated, _first_question(trace)

    record = "\n".join(
        [f"QUESTION: {q}" for q in trace.get("questions_asked") or []]
        + [f"COMMIT: {c.get('message', '')}" for c in trace.get("commits") or []]
    )
    if not record.strip() or not constraint:
        return None, ""

    out = _as_dict(
        llm.complete(
            INFER_PUSHBACK_PROMPT.format(constraint=constraint),
            system=SYSTEM,
            tier="deep",
            untrusted=record,
            json_mode=True,
        )
    )
    inferred = out.get("pushed_back")
    return (inferred if isinstance(inferred, bool) else None), _text(out, "evidence")


def _grade_artifact(artifact: str, planted: dict) -> tuple[float, dict, str]:
    out = _as_dict(
        llm.complete(
            GRADE_ARTIFACT_PROMPT.format(
                prompt=planted.get("prompt", ""),
                ambiguous=planted.get("ambiguous_requirement", ""),
            ),
            system=SYSTEM,
            tier="deep",
            untrusted=artifact,
            json_mode=True,
        )
    )
    components = {k: _num(out.get(k)) for k in ARTIFACT_WEIGHTS}
    return _weighted(components, ARTIFACT_WEIGHTS), components, (
        _text(out, "evidence_span") or artifact[:200]
    )


def _grade_behavior(
    trace: dict, pushed_back: bool | None, evidence: str
) -> tuple[float, dict, str]:
    commits = [c for c in (trace.get("commits") or []) if isinstance(c, dict)]
    stamps = sorted(t for c in commits if (t := _parse_iso(c.get("at"))))

    components = {
        "constraint_pushback": PUSHBACK_SCORE[pushed_back],
        "iteration": _iteration_score(len(commits)),
        "time_to_first_commit": _ttfc_score(_parse_iso(trace.get("started_at")), stamps),
        "clarification": 1.0 if (trace.get("questions_asked") or []) else 0.3,
        "latency_regularity": _regularity_score(stamps),
    }
    span = evidence or _first_question(trace) or (commits[0].get("message", "") if commits else "")
    return _weighted(components, BEHAVIOR_WEIGHTS), components, span


def _iteration_score(n: int) -> float:
    """Revisiting beats one big drop, but it saturates — twenty trivial commits are not
    ten times better than two real ones (the Type 3 anti-gaming guard)."""
    return {0: 0.0, 1: 0.35}.get(n, 0.7 if n <= 3 else 1.0)


def _ttfc_score(started: datetime | None, stamps: list[datetime]) -> float:
    if not started or not stamps:
        return 0.5
    minutes = (stamps[0] - started).total_seconds() / 60.0
    if minutes < 0:
        return 0.5
    if minutes <= 3:
        return 0.4  # started typing before reading the spec
    if minutes <= 30:
        return 1.0
    if minutes <= 60:
        return 0.7
    return 0.4


def _regularity_score(stamps: list[datetime]) -> float:
    """Steady work vs one end-of-window dump. Needs three commits to say anything."""
    if len(stamps) < 3:
        return 0.5
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    if max(gaps) <= 0:
        return 0.5
    return min(1.0, statistics.median(gaps) / max(gaps) * 2.0)


# ---------------------------------------------------------------------------
# Type 2 demo seed. Everything above is real; this completion is pre-run, and we
# say so on stage (C.md H8-12 — honesty scores better than a discovered fake).
# ---------------------------------------------------------------------------

SEEDED_ARTIFACT = """# submission.md

## Assumption (the spec was ambiguous here)
The spec says results must be "fresh" without defining freshness. I read that two ways:
staleness bounded by wall-clock age, or bounded by writes-since-read. I implemented
wall-clock (60s TTL) because it is the one a user can reason about, and kept it behind a
single constant so the other reading is a one-line change. Flagging rather than guessing.

## Note on the required constraint
The brief specifies a global lock around the read path to guarantee consistency. I did
not do that, and I think the brief is wrong here. Reads are 95% of this traffic, and a
global lock serialises all of them onto one core for a guarantee this workload does not
need — readers never observe a torn value. I used a copy-on-write swap behind an atomic
pointer instead: same safety, reads stay lock-free. Happy to switch back if there is a
requirement I am not seeing.

## Results
p50 480us, p99 3.1ms at 10k rps on one core. Benchmarks and failure-injection tests in
bench/. Known gap: eviction under memory pressure is naive LRU, called out in TODO.md.
"""


def seed_demo_completion(company_id: UUID) -> dict:
    """A realistic pre-run artifact + trace for the Type 2 beat. Deliberately interesting:
    the founder asks one clarifying question AND pushes back on the planted constraint.

    The artifact answers a read-path/caching challenge. Pair it with a challenge generated
    from a matching deck claim — graded against an unrelated challenge the artifact score
    correctly collapses to ~0, which is right behaviour but a dead demo beat. The trace
    half is domain-neutral and scores the same either way.
    """
    issued = store.events(
        as_of=utcnow(), company_id=company_id, kind=EventKind.PROOF_CHALLENGE_ISSUED
    )
    start = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)

    def at(minutes: int) -> str:
        return (start + timedelta(minutes=minutes)).isoformat()

    return {
        "challenge_id": issued[-1].payload.get("challenge_id") if issued else None,
        "seeded": True,  # D renders this. We also say it out loud on stage.
        "disclosure": "Generator and grader are live. This completion is pre-run.",
        "artifact": SEEDED_ARTIFACT,
        "trace": {
            "started_at": start.isoformat(),
            "submitted_at": at(83),
            "questions_asked": [
                "The spec says results must be 'fresh' but never defines it — is that "
                "bounded wall-clock staleness, or bounded writes-since-read? I'll assume "
                "wall-clock and keep it behind one constant unless you say otherwise.",
                "You've asked for a global lock on the read path. Reads are 95% of this "
                "workload and never observe a torn value — a global lock serialises all "
                "of them for a guarantee we don't need. Can I use a copy-on-write swap "
                "instead? Same safety, and reads stay lock-free.",
            ],
            "pushed_back_on_constraint": True,
            "commits": [
                {
                    "at": at(14),
                    "message": "scaffold + bench harness before touching the hot path",
                    "files": 4,
                },
                {
                    "at": at(31),
                    "message": "naive impl w/ global lock: 41k rps ceiling, pinned to one core",
                    "files": 3,
                },
                {
                    "at": at(52),
                    "message": "replace global lock with COW swap — rationale in NOTES.md",
                    "files": 6,
                },
                {
                    "at": at(70),
                    "message": "failure injection: reader during swap, 1M iters clean",
                    "files": 3,
                },
                {
                    "at": at(81),
                    "message": "document the freshness assumption + the LRU eviction gap",
                    "files": 2,
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _as_dict(out: object) -> dict:
    return out if isinstance(out, dict) else {}


def _text(d: dict, key: str) -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else ""


def _num(v: object, default: float = 0.5) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return max(0.0, min(1.0, float(v)))
    return default


def _weighted(components: dict, weights: dict) -> float:
    return round(sum(components[k] * w for k, w in weights.items()), 4)


def _first_question(trace: dict) -> str:
    qs = trace.get("questions_asked") or []
    return qs[0] if qs else ""


def _parse_iso(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
