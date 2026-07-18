"""Green-flag rules: the SENSOR feeding A's filter. Owner: C. See C.md H1-3.

30+ interpretable YES/NO rules, trajectory-tuned. Each applicable rule emits one
GREEN_FLAG event carrying its evidence span, so every score decomposes to
rules_fired + source spans.

Design constraints this file honors:
  - Applicability gating: a rule that needs a source the entity doesn't have is
    NOT evaluated (a designer with no repo history is not penalized by repo rules).
  - observed_at on an emitted flag is derived from the EVIDENCE, never now() —
    stamping now() would poison the backtest (Invariant #1).
  - Substance over volume: burst/adoption rules read substance markers in the
    payload, never raw counts alone (anti-gaming, Type 3/5 guard).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from schema.events import Event, EventKind, Source

# ---------------------------------------------------------------------------
# Observation-noise contract with A (memory/score.py). Agreed payload:
# GREEN_FLAG.payload == {rule_id, question, weight, fired, evidence_event_ids}
# y_t = weighted YES-rate over evaluated flags; r_t shrinks with evidence count.
# ---------------------------------------------------------------------------
BASE_NOISE = 0.25
NOISE_FLOOR = 0.02
NOISE_CEIL = 0.5
UNINFORMATIVE = (0.5, 0.5)  # no flags evaluated -> tell the filter nothing

# Public compatibility vocabulary shared with D's absence classifier.
ARTIFACT_KINDS = (
    EventKind.REPO_ACTIVITY,
    EventKind.COMMIT_BURST,
    EventKind.RELEASE,
    EventKind.PROOF_ARTIFACT,
    EventKind.PAPER,
)
CODE_KINDS = (
    EventKind.REPO_ACTIVITY,
    EventKind.COMMIT_BURST,
    EventKind.RELEASE,
    EventKind.PROOF_ARTIFACT,
)

_EVIDENCE_SPAN_MAX = 240

RuleCheck = Callable[[Sequence[Event]], tuple[bool, list[Event]]]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    question: str
    weight: float
    requires: frozenset[Source] | None  # None = always applicable
    check: RuleCheck

    @property
    def id(self) -> str:
        """Compatibility alias used by D's trace/pipeline presentation layer."""
        return self.rule_id


# ---------------------------------------------------------------------------
# Payload readers — defensive: sourcing payloads are per-kind dicts (SHARED §3),
# so every read is .get() with a sane default.
# ---------------------------------------------------------------------------


def _text(e: Event) -> str:
    parts = [e.evidence_span or ""]
    for key in ("title", "text", "body", "message", "description", "abstract", "claim"):
        v = e.payload.get(key)
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts).lower()


def _repo(e: Event) -> str | None:
    v = e.payload.get("repo") or e.payload.get("repo_name") or e.payload.get("repository")
    return v if isinstance(v, str) else None


def _of_kind(events: Sequence[Event], *kinds: EventKind) -> list[Event]:
    return [e for e in events if e.kind in kinds]


def _matching(events: Sequence[Event], pattern: str, *kinds: EventKind) -> list[Event]:
    rx = re.compile(pattern)
    pool = _of_kind(events, *kinds) if kinds else list(events)
    return [e for e in pool if rx.search(_text(e))]


def _distinct_months(events: Sequence[Event]) -> int:
    return len({(e.observed_at.year, e.observed_at.month) for e in events})


def _span_days(events: Sequence[Event]) -> float:
    if len(events) < 2:
        return 0.0
    ts = sorted(e.observed_at for e in events)
    return (ts[-1] - ts[0]).total_seconds() / 86400


def _flag(v: object) -> bool:
    return v is True or v == "true" or v == 1


# ---------------------------------------------------------------------------
# Rule builders — shared shapes, so 30+ rules stay readable and uniform.
# ---------------------------------------------------------------------------


def _count_rule(min_count: int, *kinds: EventKind) -> RuleCheck:
    def check(events: Sequence[Event]) -> tuple[bool, list[Event]]:
        hits = _of_kind(events, *kinds)
        return len(hits) >= min_count, hits[:min_count]

    return check


def _keyword_rule(pattern: str, *kinds: EventKind) -> RuleCheck:
    def check(events: Sequence[Event]) -> tuple[bool, list[Event]]:
        hits = _matching(events, pattern, *kinds)
        return bool(hits), hits[:3]

    return check


def _payload_rule(key: str, *kinds: EventKind, min_value: float | None = None) -> RuleCheck:
    def check(events: Sequence[Event]) -> tuple[bool, list[Event]]:
        hits = []
        for e in _of_kind(events, *kinds):
            v = e.payload.get(key)
            if min_value is not None:
                if isinstance(v, (int, float)) and v >= min_value:
                    hits.append(e)
            elif _flag(v):
                hits.append(e)
        return bool(hits), hits[:3]

    return check


def _payload_max_rule(key: str, max_value: float, *kinds: EventKind) -> RuleCheck:
    def check(events: Sequence[Event]) -> tuple[bool, list[Event]]:
        hits = [
            e
            for e in _of_kind(events, *kinds)
            if isinstance(e.payload.get(key), (int, float)) and e.payload[key] <= max_value
        ]
        return bool(hits), hits[:3]

    return check


def _repeat_release(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    """Shipped more than once, unprompted: releases in >= 2 distinct months."""
    rel = sorted(_of_kind(events, EventKind.RELEASE), key=lambda e: e.observed_at)
    fired = len(rel) >= 2 and _distinct_months(rel) >= 2
    return fired, [rel[0], rel[-1]] if fired else []


def _sustained_90d(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    acts = sorted(
        _of_kind(events, EventKind.REPO_ACTIVITY, EventKind.RELEASE, EventKind.COMMIT_BURST),
        key=lambda e: e.observed_at,
    )
    fired = _span_days(acts) >= 90
    return fired, [acts[0], acts[-1]] if fired else []


def _active_weeks(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    acts = sorted(
        _of_kind(events, EventKind.REPO_ACTIVITY, EventKind.COMMIT_BURST),
        key=lambda e: e.observed_at,
    )
    evidence_by_week = {}
    for event in acts:
        evidence_by_week.setdefault(event.observed_at.isocalendar()[:2], event)
    fired = len(evidence_by_week) >= 3
    return fired, list(evidence_by_week.values())[:3] if fired else []


def _recent_momentum_factory(as_of_days: float = 30) -> RuleCheck:
    # bound as_of at emit time via closure set in evaluate_events
    def check(events: Sequence[Event]) -> tuple[bool, list[Event]]:
        acts = _of_kind(events, EventKind.REPO_ACTIVITY, EventKind.RELEASE, EventKind.HN_POST)
        if not acts:
            return False, []
        latest = max(acts, key=lambda e: e.observed_at)
        anchor = max(e.observed_at for e in events)
        fired = (anchor - latest.observed_at).total_seconds() / 86400 <= as_of_days
        return fired, [latest]

    return check


def _iterates_same_artifact(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    """Revisiting one artifact beats starting five new ones."""
    repo_events = [
        e
        for e in _of_kind(
            events, EventKind.REPO_ACTIVITY, EventKind.RELEASE, EventKind.COMMIT_BURST
        )
        if _repo(e)
    ]
    counts = Counter(_repo(e) for e in repo_events)
    if not counts:
        return False, []
    top, n = counts.most_common(1)[0]
    return n >= 3, [e for e in repo_events if _repo(e) == top][:3]


def _revisits_over_new(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    repo_events = [
        e
        for e in _of_kind(
            events, EventKind.REPO_ACTIVITY, EventKind.RELEASE, EventKind.COMMIT_BURST
        )
        if _repo(e)
    ]
    counts = Counter(_repo(e) for e in repo_events)
    if sum(counts.values()) < 4:
        return False, []
    top, n = counts.most_common(1)[0]
    return n / sum(counts.values()) >= 0.5, [e for e in repo_events if _repo(e) == top][:3]


def _releases_same_repo(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    rel = [e for e in _of_kind(events, EventKind.RELEASE) if _repo(e)]
    counts = Counter(_repo(e) for e in rel)
    if not counts:
        return False, []
    top, n = counts.most_common(1)[0]
    return n >= 2, [e for e in rel if _repo(e) == top][:3]


def _burst_with_substance(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    """Burst alone is never the flag (Type 5 guard) — substance markers required."""
    hits = [
        e
        for e in _of_kind(events, EventKind.COMMIT_BURST)
        if _flag(e.payload.get("has_tests"))
        or (
            isinstance(e.payload.get("diff_entropy"), (int, float))
            and e.payload["diff_entropy"] >= 0.5
        )
        or (
            isinstance(e.payload.get("file_diversity"), (int, float))
            and e.payload["file_diversity"] >= 3
        )
    ]
    return bool(hits), hits[:3]


def _founder_replies(events: Sequence[Event]) -> tuple[bool, list[Event]]:
    hits = [e for e in _of_kind(events, EventKind.HN_COMMENT) if _flag(e.payload.get("is_author"))]
    return len(hits) >= 2, hits[:3]


_GH = frozenset({Source.GITHUB})
_HN = frozenset({Source.HN})
_ARXIV = frozenset({Source.ARXIV})
_DECK = frozenset({Source.DECK})
_PROOF = frozenset({Source.PROOF_PROTOCOL})
_TEXTY = frozenset({Source.GITHUB, Source.HN, Source.WEB, Source.ARXIV})

_NUMBERS_WITH_UNITS = r"\b\d[\d,.]*\s?(ms|s|qps|rps|gb|mb|tokens?/s|%|x faster|x speedup)\b"

RULES: list[Rule] = [
    # -- shipping & cadence -------------------------------------------------
    Rule(
        "shipped_release",
        "Shipped a versioned release users can install?",
        3.0,
        _GH,
        _count_rule(1, EventKind.RELEASE),
    ),
    Rule(
        "repeat_shipper",
        "Shipped more than once, unprompted, across months?",
        3.0,
        _GH,
        _repeat_release,
    ),
    Rule("sustained_activity_90d", "Kept building on this for 90+ days?", 2.0, _GH, _sustained_90d),
    Rule(
        "active_multiple_weeks", "Active in three or more distinct weeks?", 2.0, _GH, _active_weeks
    ),
    Rule(
        "recent_momentum",
        "Still shipping within the last 30 days of history?",
        1.0,
        _GH,
        _recent_momentum_factory(30),
    ),
    Rule(
        "burst_with_substance",
        "Commit burst carries real substance (tests, diverse diffs)?",
        2.0,
        _GH,
        _burst_with_substance,
    ),
    Rule(
        "tests_present",
        "Wrote automated tests for their own artifact?",
        2.0,
        _GH,
        _payload_rule(
            "has_tests", EventKind.REPO_ACTIVITY, EventKind.COMMIT_BURST, EventKind.RELEASE
        ),
    ),
    Rule(
        "ci_configured",
        "Set up continuous integration?",
        1.0,
        _GH,
        _keyword_rule(
            r"\b(ci|github actions|workflow|pipeline)\b", EventKind.REPO_ACTIVITY, EventKind.RELEASE
        ),
    ),
    Rule(
        "docs_written",
        "Wrote docs/README/changelog for users, not just code?",
        1.0,
        _GH,
        _keyword_rule(
            r"\b(readme|docs|documentation|changelog)\b", EventKind.REPO_ACTIVITY, EventKind.RELEASE
        ),
    ),
    Rule(
        "semver_discipline",
        "Versions releases deliberately (semver-shaped tags)?",
        1.0,
        _GH,
        _keyword_rule(r"\bv?\d+\.\d+\.\d+\b", EventKind.RELEASE),
    ),
    # -- iteration on the same artifact ------------------------------------
    Rule(
        "iterates_same_artifact",
        "Returned to the same artifact three or more times?",
        3.0,
        _GH,
        _iterates_same_artifact,
    ),
    Rule(
        "revisits_over_new",
        "Revisits existing work more than starting new repos?",
        2.0,
        _GH,
        _revisits_over_new,
    ),
    Rule(
        "releases_same_repo",
        "Multiple releases of the SAME artifact?",
        3.0,
        _GH,
        _releases_same_repo,
    ),
    # -- learning from failure ----------------------------------------------
    Rule(
        "rewrite_evidence",
        "Rewrote or fundamentally reworked an approach?",
        2.0,
        _TEXTY,
        _keyword_rule(r"\b(rewrite|rewrote|rewritten|refactor(ed)?|from scratch|migrated)\b"),
    ),
    Rule(
        "postmortem_written",
        "Wrote a postmortem or lessons-learned in public?",
        3.0,
        _TEXTY,
        _keyword_rule(r"\b(postmortem|post-mortem|lessons learned|what went wrong)\b"),
    ),
    Rule(
        "reverted_course",
        "Visibly reverted or abandoned a failed approach?",
        1.0,
        _TEXTY,
        _keyword_rule(r"\b(revert(ed)?|rolled back|abandoned|didn't work|did not work)\b"),
    ),
    Rule(
        "responds_to_feedback",
        "Engages with feedback in their own threads?",
        2.0,
        _HN,
        _founder_replies,
    ),
    # -- ambiguity -> concrete scoping --------------------------------------
    Rule(
        "scoped_vague_problem",
        "Scoped a vague problem into a concrete one?",
        2.0,
        _TEXTY,
        _keyword_rule(r"\b(scoped|narrowed|focus(ed)? on|specifically|instead of solving)\b"),
    ),
    Rule(
        "states_assumptions",
        "States assumptions explicitly?",
        1.0,
        _TEXTY,
        _keyword_rule(r"\bassum(e|es|ed|ing|ption)"),
    ),
    Rule(
        "defines_non_goals",
        "Names non-goals / what's out of scope?",
        1.0,
        _TEXTY,
        _keyword_rule(r"\b(non-goal|out of scope|explicitly not|we don't try)\b"),
    ),
    Rule(
        "concrete_metrics_cited",
        "Cites concrete measured numbers, not adjectives?",
        2.0,
        _TEXTY,
        _keyword_rule(_NUMBERS_WITH_UNITS),
    ),
    # -- technical depth relative to the problem ----------------------------
    Rule(
        "published_research",
        "Published research on the problem they're attacking?",
        2.0,
        _ARXIV,
        _count_rule(1, EventKind.PAPER),
    ),
    Rule(
        "research_with_artifact",
        "Research comes with a runnable artifact?",
        2.0,
        _ARXIV,
        _keyword_rule(r"\b(github\.com|code available|open[- ]?source(d)?)\b", EventKind.PAPER),
    ),
    Rule(
        "infra_domain_depth",
        "Work sits in genuinely hard infra territory?",
        3.0,
        _TEXTY,
        _keyword_rule(
            r"\b(compiler|kernel|inference|scheduler|distributed|gpu|cuda|"
            r"quantization|runtime|allocator|jit|vectoriz)\w*\b"
        ),
    ),
    Rule(
        "benchmarks_published",
        "Published benchmarks with real numbers?",
        2.0,
        _TEXTY,
        _keyword_rule(r"\b(benchmark|latency|throughput)\b"),
    ),
    Rule(
        "explains_tradeoffs",
        "Explains trade-offs of their design choices?",
        2.0,
        _TEXTY,
        _keyword_rule(r"\b(trade-?off|at the cost of|we chose .* because|downside)\b"),
    ),
    # -- users touching the artifact -----------------------------------------
    Rule(
        "show_hn_ship",
        "Put an artifact in front of strangers (Show HN)?",
        2.0,
        _HN,
        _keyword_rule(r"\bshow hn\b", EventKind.HN_POST),
    ),
    Rule(
        "external_contributors",
        "Strangers contribute to their artifact?",
        2.0,
        _GH,
        _payload_rule("contributors", EventKind.REPO_ACTIVITY, EventKind.RELEASE, min_value=2),
    ),
    Rule(
        "answers_technical_questions",
        "Answers technical questions in public?",
        1.0,
        _HN,
        _count_rule(3, EventKind.HN_COMMENT),
    ),
    # -- proof protocol (low-noise, behavioral) ------------------------------
    Rule(
        "proof_artifact_works",
        "Proof challenge artifact actually works?",
        4.0,
        _PROOF,
        _payload_rule("works", EventKind.PROOF_ARTIFACT),
    ),
    Rule(
        "proof_pushed_back",
        "Pushed back on the planted bad constraint?",
        5.0,
        _PROOF,
        _payload_rule("challenged_bad_constraint", EventKind.PROOF_BEHAVIOR),
    ),
    Rule(
        "proof_surfaced_ambiguity",
        "Asked about (or explicitly assumed) the ambiguity?",
        4.0,
        _PROOF,
        _payload_rule("asked_clarifying", EventKind.PROOF_BEHAVIOR),
    ),
    Rule(
        "proof_iterated",
        "Iterated on the challenge rather than one-shotting?",
        2.0,
        _PROOF,
        _payload_rule("iteration_count", EventKind.PROOF_BEHAVIOR, min_value=3),
    ),
    Rule(
        "proof_fast_start",
        "Started building within 30 minutes of receiving it?",
        1.0,
        _PROOF,
        _payload_max_rule("time_to_first_commit_min", 30, EventKind.PROOF_BEHAVIOR),
    ),
    # -- consistency ----------------------------------------------------------
]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


# Integrity flags that make an event unusable as evidence, as opposed to merely
# annotating where it came from.
#
# This distinction is load-bearing. Excluding every event with ANY integrity flag
# meant that `transliterated_name` — set on 100% of the events belonging to the
# non-Latin-script founders — silently voided their entire evidence base. All three
# scored at the untouched prior, which reads as "average founder" rather than "we
# could not read this", and the system built to find founders others cannot see was
# systematically blind to exactly them. D.md names this as the guarded failure for
# that archetype: silent low-scoring via degraded extraction confidence.
#
# A stripped injection means the content was tampered with and cannot be trusted.
# A transliterated name, a non-English source or an inferred date are notes about
# provenance: they belong in the memo and the trace, never in a decision to ignore
# the evidence.
IMPEACHING_FLAGS: frozenset[str] = frozenset(
    {"injection_stripped", "prompt_injection", "content_tampered", "unattested_trace"}
)


def evaluate_events(events: list[Event], entity_id: UUID, as_of: datetime) -> list[Event]:
    """Pure core: as_of-scoped events in, one GREEN_FLAG event per APPLICABLE rule out.

    Rules whose required sources are absent are not evaluated at all — absence of
    a source is handled by the gate's absence classifier, never by a failing flag.
    """
    events = [
        e
        for e in events
        if e.entity_id == entity_id
        and e.observed_at <= as_of
        and e.kind not in {EventKind.GREEN_FLAG, EventKind.INTEGRITY}
        and not (set(e.integrity_flags) & IMPEACHING_FLAGS)
    ]
    if not events:
        return []
    present_sources = {e.source for e in events}
    anchor = max(e.observed_at for e in events)  # never now(): Invariant #1

    flags: list[Event] = []
    for rule in RULES:
        if rule.requires is not None and not (rule.requires & present_sources):
            continue
        fired, evidence = rule.check(events)
        # Fired flags are stamped when their evidence happened; unfired flags carry
        # no evidence claim, so they anchor to the latest event we looked at.
        observed_at = max((e.observed_at for e in evidence), default=anchor) if fired else anchor
        span = None
        if fired and evidence:
            src = evidence[0]
            span = (src.evidence_span or _text(src))[:_EVIDENCE_SPAN_MAX] or None
        confidence = min((e.confidence for e in evidence), default=1.0)
        flags.append(
            Event(
                entity_id=entity_id,
                kind=EventKind.GREEN_FLAG,
                source=evidence[0].source if evidence else Source.MANUAL,
                source_url=evidence[0].source_url if evidence else None,
                observed_at=observed_at,
                payload={
                    "rule_id": rule.rule_id,
                    "question": rule.question,
                    "weight": rule.weight,
                    "fired": fired,
                    "evidence_event_ids": [str(e.event_id) for e in evidence],
                },
                evidence_span=span,
                confidence=confidence,
            )
        )
    return flags


def evaluate(
    entity_id: UUID, as_of: datetime, events: Sequence[Event] | None = None
) -> list[Event]:
    """Store wrapper plus the scalar rollup consumed by A/D's scoring pipeline."""
    if events is None:
        from memory import store

        history = store.events(entity_id=entity_id, as_of=as_of)
    else:
        history = list(events)
    scoped = [
        event
        for event in history
        if event.entity_id == entity_id
        and event.observed_at <= as_of
        and event.kind not in {EventKind.GREEN_FLAG, EventKind.INTEGRITY}
        # Same rule as evaluate_events: only IMPEACHING flags disqualify evidence.
        # This filter is duplicated here, so leaving it un-fixed kept the entire
        # non-Latin-script cohort at zero flags even after the core was corrected.
        and not (set(event.integrity_flags) & IMPEACHING_FLAGS)
    ]
    per_rule = evaluate_events(scoped, entity_id=entity_id, as_of=as_of)
    if not scoped:
        return per_rule

    y_t, _ = observation(per_rule)
    by_rule = {event.payload["rule_id"]: event for event in per_rule}
    flag_rows = [
        {
            "id": rule.rule_id,
            "fired": (
                bool(by_rule[rule.rule_id].payload.get("fired"))
                if rule.rule_id in by_rule
                else False
            ),
            "weight": rule.weight,
            "applicable": rule.rule_id in by_rule,
        }
        for rule in RULES
    ]
    fired = [row["id"] for row in flag_rows if row["applicable"] and row["fired"]]
    anchor = max(event.observed_at for event in scoped)
    company_ids = {event.company_id for event in scoped if event.company_id is not None}
    if len(company_ids) > 1:
        return per_rule
    company_id = next(iter(company_ids), None)
    self_consistency = sum(event.confidence for event in per_rule) / max(len(per_rule), 1)
    source_evidence_ids = sorted(
        {
            str(evidence_id)
            for event in per_rule
            for evidence_id in event.payload.get("evidence_event_ids", [])
        }
    )
    rollup = Event(
        entity_id=entity_id,
        company_id=company_id,
        kind=EventKind.GREEN_FLAG,
        source=Source.MANUAL,
        observed_at=anchor,
        payload={
            "value": y_t,
            "y": y_t,
            "flags": flag_rows,
            "rules_fired": fired,
            "rollup": True,
            "self_consistency": self_consistency,
            "observation_role": "rollup",
            "derived_from_event_ids": [str(event.event_id) for event in per_rule],
            "source_evidence_event_ids": source_evidence_ids,
        },
        evidence_span=f"{len(fired)}/{len(per_rule)} applicable green flags fired",
        confidence=self_consistency,
    )
    return [*per_rule, rollup]


def observation(flag_events: list[Event]) -> tuple[float, float]:
    """(y_t, r_t) for A's filter — the agreed contract (see header comment).

    y_t: weighted YES-rate over evaluated flags. r_t: observation noise, shrinking
    with evidence count and extraction confidence. No flags -> uninformative prior.
    """
    scored = [e for e in flag_events if e.kind == EventKind.GREEN_FLAG and "rule_id" in e.payload]
    if not scored:
        return UNINFORMATIVE

    total_weight = sum(float(e.payload.get("weight", 0.0)) for e in scored)
    if total_weight <= 0:
        return UNINFORMATIVE
    fired_weight = sum(
        float(e.payload.get("weight", 0.0)) for e in scored if _flag(e.payload.get("fired"))
    )
    y_t = fired_weight / total_weight

    mean_conf = sum(e.confidence for e in scored) / len(scored)
    n_effective = max(len(scored) * mean_conf, 1e-9)
    r_t = min(max(BASE_NOISE / math.sqrt(n_effective), NOISE_FLOOR), NOISE_CEIL)
    return y_t, r_t
