"""Green-flag rules: the SENSOR feeding A's filter. Owner: C. See C.md H1-3.

48 interpretable YES/NO rules, trajectory-tuned. Each fired rule emits its own
GREEN_FLAG event carrying the evidence span that fired it, so every score
decomposes to rules_fired + source spans.

Two event shapes come out of evaluate():
  - one per fired rule -> the decomposition D renders. Deliberately carries NO
    scalar, so memory/score.py's _derive_y skips it and the receipts never
    double-count against the rollup.
  - one rollup -> the observation. Payload carries value + y + flags, all three
    shapes A accepts, so the two implementations cannot mismatch.

Invariant #3: these rules look at what was built, how often it was revisited and
what was learned. None of that needs a resume.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from schema.events import Event, EventKind, Source

log = logging.getLogger(__name__)

# Kinds that represent a durable thing that was built, as opposed to talk about it.
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

RECENT_DAYS = 90
SPAN_CHARS = 180

# Noise model. R_BASE matches memory/score.py's R0; a thin history reads noisier.
R_BASE = 0.01
R_THINNESS = 3.0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """A rule fired, and this is what fired it."""

    span: str
    observed_at: datetime
    event_ids: tuple[UUID, ...]


def _snippet(ev: Event) -> str:
    if ev.evidence_span:
        return ev.evidence_span[:SPAN_CHARS]
    body = " ".join(str(v) for v in ev.payload.values() if isinstance(v, (str, int, float)))
    return (body or str(ev.kind))[:SPAN_CHARS]


def hit(note: str, *events: Event | None) -> Hit | None:
    """Build a Hit from the events that satisfied a rule. No events -> no hit."""
    evs = [e for e in events if e is not None]
    if not evs:
        return None
    latest = max(evs, key=lambda e: e.observed_at)
    return Hit(
        span=f"{note} — {_snippet(latest)}",
        observed_at=latest.observed_at,
        event_ids=tuple(e.event_id for e in evs),
    )


# ---------------------------------------------------------------------------
# Feature view. Built once per evaluation; every rule is a predicate over it.
# Payload shapes come from B in parallel, so every read here is best-effort.
# ---------------------------------------------------------------------------


def _num(ev: Event, *keys: str) -> float:
    for k in keys:
        v = ev.payload.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return 0.0


def _text_of(ev: Event) -> str:
    parts = [ev.evidence_span or ""] + [str(v) for v in ev.payload.values() if isinstance(v, str)]
    return " ".join(parts).lower()


def _artifact_key(ev: Event) -> str | None:
    for k in ("repo", "repo_full_name", "artifact", "project", "package", "product"):
        v = ev.payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return ev.source_url.lower() if ev.source_url else None


@dataclass
class Signals:
    events: list[Event]
    as_of: datetime
    by_kind: dict[str, list[Event]]
    artifacts: dict[str, list[Event]]  # artifact key -> its events, chronological
    text: dict[UUID, str]

    @classmethod
    def build(cls, events: Sequence[Event], as_of: datetime) -> Signals:
        evs = sorted(events, key=lambda e: e.observed_at)
        artifact_kinds = {str(k) for k in ARTIFACT_KINDS}
        by_kind: dict[str, list[Event]] = {}
        artifacts: dict[str, list[Event]] = {}
        for e in evs:
            by_kind.setdefault(str(e.kind), []).append(e)
            if str(e.kind) in artifact_kinds and (key := _artifact_key(e)):
                artifacts.setdefault(key, []).append(e)
        return cls(evs, as_of, by_kind, artifacts, {e.event_id: _text_of(e) for e in evs})

    def of(self, *kinds: EventKind) -> list[Event]:
        return [e for k in kinds for e in self.by_kind.get(str(k), [])]

    def mentions(
        self, phrases: Iterable[str], *, kinds: Iterable[EventKind] | None = None
    ) -> Event | None:
        pool = self.of(*kinds) if kinds else self.events
        return next(
            (e for e in pool if any(p in self.text.get(e.event_id, "") for p in phrases)), None
        )

    def at_least(
        self, keys: Sequence[str], threshold: float, *, kinds: Iterable[EventKind] | None = None
    ) -> Event | None:
        pool = self.of(*kinds) if kinds else self.events
        return next((e for e in pool if _num(e, *keys) >= threshold), None)

    def marked(self, keys: Sequence[str]) -> Event | None:
        return next((e for e in self.events if any(bool(e.payload.get(k)) for k in keys)), None)

    def recent(self, evs: Sequence[Event], days: int = RECENT_DAYS) -> list[Event]:
        cutoff = self.as_of - timedelta(days=days)
        return [e for e in evs if e.observed_at >= cutoff]

    @property
    def top_artifact(self) -> list[Event]:
        return max(self.artifacts.values(), key=len) if self.artifacts else []

    @property
    def substantive(self) -> list[Event]:
        return [e for e in self.events if str(e.kind) != str(EventKind.PROFILE_FACT)]


def _span_days(evs: Sequence[Event]) -> float:
    if len(evs) < 2:
        return 0.0
    return (evs[-1].observed_at - evs[0].observed_at).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# The rules. Each returns a Hit (YES, with evidence) or None (NO).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: str
    question: str
    weight: float
    theme: str
    check: Callable[[Signals], Hit | None]


# -- theme 1: shipped something users touch, unprompted, more than once --------


def _shipped_public(s: Signals) -> Hit | None:
    reachable = s.of(EventKind.RELEASE) + [e for e in s.of(EventKind.REPO_ACTIVITY) if e.source_url]
    return hit("a public artifact anyone can reach", *reachable[:1])


def _repeat_usage(s: Signals) -> Hit | None:
    return hit("someone came back", s.at_least(("returning_users", "repeat_users", "retained"), 1))


def _unprompted_usage(s: Signals) -> Hit | None:
    return hit("the usage was not solicited", s.marked(("unprompted", "organic", "inbound")))


def _usage_growth(s: Signals) -> Hit | None:
    seen = [(e, _num(e, "active_users", "wau", "dau", "users")) for e in s.events]
    seen = [(e, v) for e, v in seen if v > 0]
    if len(seen) >= 2 and seen[-1][1] > seen[0][1]:
        return hit("usage rose between two separate observations", seen[0][0], seen[-1][0])
    return None


def _external_users(s: Signals) -> Hit | None:
    return hit("users beyond the builder's own circle", s.at_least(("external_users",), 1))


def _outside_contributors(s: Signals) -> Hit | None:
    return hit("other people contributed", s.at_least(("contributors", "external_contributors"), 2))


def _inbound_reports(s: Signals) -> Hit | None:
    return hit("strangers cared enough to file issues", s.at_least(("issues_opened", "bug_reports"), 1))


def _artifact_alive(s: Signals) -> Hit | None:
    return hit("the artifact is still being touched", *s.recent(s.top_artifact)[-1:])


def _built_before_pitching(s: Signals) -> Hit | None:
    build, deck = s.of(*CODE_KINDS), s.of(EventKind.DECK_CLAIM)
    if build and deck and build[0].observed_at < deck[0].observed_at:
        return hit("built it before pitching it", build[0], deck[0])
    return None


def _multiple_releases(s: Signals) -> Hit | None:
    for evs in s.artifacts.values():
        rel = [e for e in evs if str(e.kind) == str(EventKind.RELEASE)]
        if len(rel) >= 2:
            return hit("shipped the same thing more than once", *rel[:2])
    return None


# -- theme 2: iteration velocity on the SAME artifact -------------------------


def _revisits_artifact(s: Signals) -> Hit | None:
    top = s.top_artifact
    return hit("came back to the same artifact repeatedly", *top[:3]) if len(top) >= 3 else None


def _revisit_dominates(s: Signals) -> Hit | None:
    total = sum(len(v) for v in s.artifacts.values())
    top = s.top_artifact
    if total >= 4 and len(top) / total >= 0.5:
        return hit("effort concentrated on one artifact, not spread over many", *top[:2])
    return None


def _sustained_month(s: Signals) -> Hit | None:
    top = s.top_artifact
    return hit("stayed on one artifact for over a month", *top[:2]) if _span_days(top) >= 30 else None


def _sustained_quarter(s: Signals) -> Hit | None:
    top = s.top_artifact
    return hit("stayed on one artifact for four months plus", *top[:2]) if _span_days(top) >= 120 else None


def _returns_after_gap(s: Signals) -> Hit | None:
    top = s.top_artifact
    for a, b in zip(top, top[1:]):
        if (b.observed_at - a.observed_at).days > 14:
            return hit("went quiet on it and came back anyway", a, b)
    return None


def _depth_over_breadth(s: Signals) -> Hit | None:
    if sum(len(v) for v in s.artifacts.values()) >= 10 and len(s.artifacts) <= 3:
        return hit("depth on a few things rather than scatter across many", *s.top_artifact[:2])
    return None


def _regular_cadence(s: Signals) -> Hit | None:
    top = s.top_artifact
    weeks = {e.observed_at.isocalendar()[:2] for e in top}
    return hit("worked it across four or more distinct weeks", *top[:2]) if len(weeks) >= 4 else None


def _oldest_still_alive(s: Signals) -> Hit | None:
    if not s.artifacts:
        return None
    oldest = min(s.artifacts.values(), key=lambda v: v[0].observed_at)
    recent = s.recent(oldest, days=60)
    return hit("the oldest thing they built is still alive", oldest[0], recent[-1]) if recent else None


def _release_after_iteration(s: Signals) -> Hit | None:
    for evs in s.artifacts.values():
        rel = next((e for e in evs if str(e.kind) == str(EventKind.RELEASE)), None)
        if rel and len([e for e in evs if e.observed_at < rel.observed_at]) >= 3:
            return hit("the release followed real iteration rather than replacing it", rel)
    return None


def _no_serial_abandonment(s: Signals) -> Hit | None:
    singles = [v for v in s.artifacts.values() if len(v) == 1]
    if len(s.artifacts) >= 2 and len(singles) < len(s.artifacts) / 2:
        return hit("does not start things and drop them", *s.top_artifact[:1])
    return None


# -- theme 3: scoped a vague problem into a concrete one ----------------------


def _quantified_problem(s: Signals) -> Hit | None:
    units = ("%", " hours", " minutes", " per week", " per month", "x faster", "x cheaper")
    return hit("the problem is stated in numbers", s.mentions(units, kinds=(EventKind.DECK_CLAIM, EventKind.HN_POST)))


def _narrowed_scope(s: Signals) -> Hit | None:
    return hit("scope got narrower over time", s.mentions(("narrowed", "we cut", "we dropped", "instead of trying to")))


def _named_segment(s: Signals) -> Hit | None:
    return hit("names exactly who it is for", s.mentions(("specifically for", "just for", "aimed at", "our users are")))


def _states_non_goals(s: Signals) -> Hit | None:
    return hit("says what it will not do", s.mentions(("we don't", "we do not", "out of scope", "not solving", "non-goal")))


def _success_metric(s: Signals) -> Hit | None:
    return hit("defines how they would know it worked", s.mentions(("measured by", "success is", "we track", "our metric")))


def _concrete_over_abstract(s: Signals) -> Hit | None:
    vague = ("revolutionize", "disrupt", "next generation", "world-class", "cutting edge", "synergy")
    deck = s.of(EventKind.DECK_CLAIM)
    if deck and not any(v in s.text.get(e.event_id, "") for e in deck for v in vague):
        return hit("describes the thing rather than the ambition", deck[0])
    return None


def _lived_the_problem(s: Signals) -> Hit | None:
    return hit("hit the problem themselves", s.mentions(("i ran into", "we hit this", "i kept having to", "i needed this")))


def _has_wedge(s: Signals) -> Hit | None:
    return hit("has a first, narrow beachhead", s.mentions(("start with", "wedge", "first customer", "beachhead", "first ten")))


def _states_assumption(s: Signals) -> Hit | None:
    return hit("states its assumptions out loud", s.mentions(("we assume", "assumption", "this only holds if")))


def _asks_before_assuming(s: Signals) -> Hit | None:
    return hit("asked rather than guessed at an ambiguous requirement", s.at_least(("questions_asked", "clarifications"), 1))


# -- theme 4: technical depth relative to the PROBLEM -------------------------


def _tests_present(s: Signals) -> Hit | None:
    return hit("wrote tests", s.marked(("has_tests", "tests_present"))) or hit(
        "wrote tests", s.mentions(("unit test", "test suite", "regression test"))
    )


def _handles_failure_modes(s: Signals) -> Hit | None:
    return hit("thought about how it breaks", s.mentions(("retry", "idempoten", "timeout", "backpressure", "race condition", "failover")))


def _measures_performance(s: Signals) -> Hit | None:
    return hit("measured rather than guessed", s.mentions(("benchmark", "p99", "latency", "throughput", "profil")))


def _depth_matches_problem(s: Signals) -> Hit | None:
    deck, build = s.of(EventKind.DECK_CLAIM), s.of(*CODE_KINDS)
    if not deck or not build:
        return None
    words = {w for e in deck for w in s.text.get(e.event_id, "").split() if len(w) > 6}
    for e in build:
        if words & set(s.text.get(e.event_id, "").split()):
            return hit("the depth is aimed at this problem, not a generic one", deck[0], e)
    return None


def _non_trivial_change(s: Signals) -> Hit | None:
    return hit("changes are substantive rather than cosmetic", s.at_least(("lines_changed", "diff_size"), 200)) or hit(
        "changes are substantive rather than cosmetic", s.at_least(("diff_entropy",), 0.6)
    )


def _reads_others_code(s: Signals) -> Hit | None:
    return hit("engages with code they did not write", s.at_least(("reviews", "prs_reviewed", "upstream_prs"), 1))


def _wrote_for_others(s: Signals) -> Hit | None:
    return hit("wrote it down so someone else could use it", s.mentions(("readme", "design doc", "documentation", "docs/")))


def _technical_writeup(s: Signals) -> Hit | None:
    return hit(
        "published a technical explanation",
        s.mentions(("how it works", "under the hood", "architecture", "we implemented"), kinds=(EventKind.HN_POST, EventKind.PAPER)),
    )


def _substantive_discussion(s: Signals) -> Hit | None:
    long_form = [e for e in s.of(EventKind.HN_COMMENT) if len(s.text.get(e.event_id, "")) > 400]
    return hit("argues technical points at length in public", *long_form[:1])


def _explains_tradeoffs(s: Signals) -> Hit | None:
    return hit("chose deliberately and can say why", s.mentions(("tradeoff", "trade-off", "we chose", "because it was simpler", "instead we used")))


def _pushed_back(s: Signals) -> Hit | None:
    return hit("pushed back on a constraint that was wrong", s.marked(("challenged_constraint", "pushed_back")))


# -- theme 5: learning from failure -------------------------------------------


def _rewrote(s: Signals) -> Hit | None:
    return hit("threw away work that was not right", s.mentions(("rewrite", "rewrote", "refactor", "v2 of", "migrated off", "ripped out")))


def _reverted(s: Signals) -> Hit | None:
    return hit("backed out an approach that did not hold", s.mentions(("revert", "rolled back", "backed out", "abandoned that approach")))


def _postmortem(s: Signals) -> Hit | None:
    return hit("wrote up what went wrong", s.mentions(("postmortem", "post-mortem", "what went wrong", "retrospective", "lessons learned")))


def _owns_mistake(s: Signals) -> Hit | None:
    return hit("names their own mistake", s.mentions(("i was wrong", "my mistake", "i misjudged", "we got this wrong", "i underestimated")))


def _changed_mind(s: Signals) -> Hit | None:
    return hit("changed their mind when the evidence moved", s.mentions(("changed my mind", "the data showed", "turned out that", "we were surprised")))


def _kept_going_after_failure(s: Signals) -> Hit | None:
    fail = s.mentions(("failed", "shut down", "did not work", "killed the"))
    if not fail:
        return None
    after = [e for e in s.of(*CODE_KINDS) if e.observed_at > fail.observed_at + timedelta(days=14)]
    return hit("kept building after something failed", fail, *after[:1]) if after else None


def _iterated(s: Signals) -> Hit | None:
    return hit("iterated rather than one-shotting it", s.at_least(("iterations", "iteration_count"), 3))


def _fixed_own_bug(s: Signals) -> Hit | None:
    return hit("found and fixed their own bug", s.mentions(("my bug", "fixes the regression", "fix regression", "i introduced")))


RULES: list[Rule] = [
    Rule("ship.public_artifact", "Is there a public artifact anyone can reach?", 1.5, "shipped", _shipped_public),
    Rule("ship.repeat_usage", "Did anyone come back and use it a second time?", 2.0, "shipped", _repeat_usage),
    Rule("ship.unprompted", "Was the usage unprompted rather than solicited?", 2.0, "shipped", _unprompted_usage),
    Rule("ship.usage_growth", "Did usage rise between two separate observations?", 1.5, "shipped", _usage_growth),
    Rule("ship.external_users", "Are the users beyond the builder's own circle?", 1.8, "shipped", _external_users),
    Rule("ship.outside_contributors", "Did other people contribute to it?", 1.2, "shipped", _outside_contributors),
    Rule("ship.inbound_reports", "Did strangers care enough to file issues?", 1.2, "shipped", _inbound_reports),
    Rule("ship.still_alive", "Is the artifact still being touched?", 1.3, "shipped", _artifact_alive),
    Rule("ship.built_before_pitch", "Did they build it before they pitched it?", 1.5, "shipped", _built_before_pitching),
    Rule("ship.multiple_releases", "Did they ship the same thing more than once?", 1.4, "shipped", _multiple_releases),
    Rule("iter.revisits_artifact", "Do they return to the same artifact repeatedly?", 2.0, "iteration", _revisits_artifact),
    Rule("iter.revisit_dominates", "Does revisiting outweigh starting new things?", 1.8, "iteration", _revisit_dominates),
    Rule("iter.sustained_month", "Did they stay on one artifact for over a month?", 1.4, "iteration", _sustained_month),
    Rule("iter.sustained_quarter", "Did they stay on one artifact for four months plus?", 1.6, "iteration", _sustained_quarter),
    Rule("iter.returns_after_gap", "Did they go quiet on it and come back anyway?", 1.5, "iteration", _returns_after_gap),
    Rule("iter.depth_over_breadth", "Is the effort concentrated rather than scattered?", 1.3, "iteration", _depth_over_breadth),
    Rule("iter.regular_cadence", "Did they work it across four or more distinct weeks?", 1.2, "iteration", _regular_cadence),
    Rule("iter.oldest_alive", "Is the oldest thing they built still alive?", 1.3, "iteration", _oldest_still_alive),
    Rule("iter.release_after_iteration", "Did the release follow real iteration?", 1.1, "iteration", _release_after_iteration),
    Rule("iter.no_abandonment", "Do they finish things rather than abandon them?", 1.4, "iteration", _no_serial_abandonment),
    Rule("scope.quantified", "Is the problem stated in numbers?", 1.2, "scoping", _quantified_problem),
    Rule("scope.narrowed", "Did the scope get narrower over time?", 1.6, "scoping", _narrowed_scope),
    Rule("scope.named_segment", "Do they name exactly who it is for?", 1.3, "scoping", _named_segment),
    Rule("scope.non_goals", "Do they say what they will not do?", 1.4, "scoping", _states_non_goals),
    Rule("scope.success_metric", "Do they define how they would know it worked?", 1.3, "scoping", _success_metric),
    Rule("scope.concrete", "Do they describe the thing rather than the ambition?", 1.1, "scoping", _concrete_over_abstract),
    Rule("scope.lived_problem", "Did they hit this problem themselves?", 1.4, "scoping", _lived_the_problem),
    Rule("scope.wedge", "Is there a first, narrow beachhead?", 1.3, "scoping", _has_wedge),
    Rule("scope.states_assumption", "Do they state their assumptions out loud?", 1.2, "scoping", _states_assumption),
    Rule("scope.asks_first", "Do they ask when a requirement is ambiguous?", 1.5, "scoping", _asks_before_assuming),
    Rule("depth.tests", "Did they write tests?", 1.1, "depth", _tests_present),
    Rule("depth.failure_modes", "Did they think about how it breaks?", 1.4, "depth", _handles_failure_modes),
    Rule("depth.measured", "Did they measure rather than guess?", 1.4, "depth", _measures_performance),
    Rule("depth.matches_problem", "Is the depth aimed at this problem specifically?", 1.7, "depth", _depth_matches_problem),
    Rule("depth.non_trivial", "Are the changes substantive rather than cosmetic?", 1.3, "depth", _non_trivial_change),
    Rule("depth.reads_others_code", "Do they engage with code they did not write?", 1.0, "depth", _reads_others_code),
    Rule("depth.wrote_for_others", "Did they write it down so someone else could use it?", 1.0, "depth", _wrote_for_others),
    Rule("depth.writeup", "Did they publish a technical explanation?", 1.1, "depth", _technical_writeup),
    Rule("depth.discussion", "Do they argue technical points at length in public?", 0.9, "depth", _substantive_discussion),
    Rule("depth.tradeoffs", "Can they say why they chose what they chose?", 1.5, "depth", _explains_tradeoffs),
    Rule("depth.pushed_back", "Did they push back on a constraint that was wrong?", 2.0, "depth", _pushed_back),
    Rule("learn.rewrote", "Did they throw away work that was not right?", 1.6, "learning", _rewrote),
    Rule("learn.reverted", "Did they back out an approach that did not hold?", 1.5, "learning", _reverted),
    Rule("learn.postmortem", "Did they write up what went wrong?", 1.4, "learning", _postmortem),
    Rule("learn.owns_mistake", "Do they name their own mistake?", 1.5, "learning", _owns_mistake),
    Rule("learn.changed_mind", "Did they change their mind when the evidence moved?", 1.5, "learning", _changed_mind),
    Rule("learn.persisted", "Did they keep building after something failed?", 1.7, "learning", _kept_going_after_failure),
    Rule("learn.iterated", "Did they iterate rather than one-shot it?", 1.3, "learning", _iterated),
    Rule("learn.fixed_own_bug", "Did they find and fix their own bug?", 1.0, "learning", _fixed_own_bug),
]

TOTAL_WEIGHT = sum(r.weight for r in RULES)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _timeline(entity_id: UUID, as_of: datetime) -> list[Event]:
    try:
        from memory import queries

        return queries.timeline(entity_id, as_of)
    except Exception as exc:  # store not up, or no rows — degrade, never crash
        log.warning("flags: timeline unavailable for %s (%s)", entity_id, exc)
        return []


def evaluate(entity_id: UUID, as_of: datetime, events: Sequence[Event] | None = None) -> list[Event]:
    """Run every rule; emit one GREEN_FLAG per fired rule, plus one rollup observation."""
    history = list(events) if events is not None else _timeline(entity_id, as_of)
    sig = Signals.build(history, as_of)

    out: list[Event] = []
    flags: list[dict] = []
    fired: list[str] = []
    numerator = 0.0
    latest: datetime | None = None

    for rule in RULES:
        try:
            h = rule.check(sig)
        except Exception as exc:  # one malformed payload must not void the whole read
            log.debug("flags: rule %s raised on %s (%s)", rule.id, entity_id, exc)
            h = None
        flags.append(
            {"id": rule.id, "question": rule.question, "fired": h is not None, "weight": rule.weight}
        )
        if h is None:
            continue
        numerator += rule.weight
        fired.append(rule.id)
        latest = max(latest, h.observed_at) if latest else h.observed_at
        out.append(
            Event(
                entity_id=entity_id,
                kind=EventKind.GREEN_FLAG,
                source=Source.MANUAL,
                observed_at=h.observed_at,
                evidence_span=h.span,
                # No scalar here on purpose: A's _derive_y skips this shape, so the
                # per-rule receipts never double-count against the rollup below.
                payload={
                    "rule_id": rule.id,
                    "question": rule.question,
                    "theme": rule.theme,
                    "weight": rule.weight,
                    "fired": True,
                    "evidence_event_ids": [str(i) for i in h.event_ids],
                },
            )
        )

    y = numerator / TOTAL_WEIGHT if TOTAL_WEIGHT else 0.0
    out.append(
        Event(
            entity_id=entity_id,
            kind=EventKind.GREEN_FLAG,
            source=Source.MANUAL,
            observed_at=min(latest or as_of, as_of),
            evidence_span=f"{len(fired)}/{len(RULES)} green flags fired: {', '.join(fired)}",
            # All three shapes A's _derive_y accepts, so the two sides cannot mismatch.
            payload={
                "value": y,
                "y": y,
                "flags": flags,
                "rules_fired": fired,
                "rollup": True,
                "self_consistency": max(min(len(sig.substantive) / 12.0, 1.0), 0.15),
            },
        )
    )
    return out


def observation(flag_events: list[Event]) -> tuple[float, float]:
    """(y_t, r_t) for A's filter. y_t = weighted YES-rate, r_t = observation noise."""
    y: float | None = None
    n_fired = 0

    for ev in flag_events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        rows = payload.get("flags")
        if isinstance(rows, list) and rows:
            num = sum(float(f.get("weight", 1.0)) for f in rows if f.get("fired"))
            den = sum(float(f.get("weight", 1.0)) for f in rows) or 1.0
            y, n_fired = num / den, sum(1 for f in rows if f.get("fired"))
            break

    if y is None:
        # Only per-rule receipts on hand: the denominator is then the full rule set.
        rows = [e for e in flag_events if e.payload.get("fired") and e.payload.get("rule_id")]
        n_fired = len(rows)
        y = sum(float(e.payload.get("weight", 1.0)) for e in rows) / TOTAL_WEIGHT

    return min(max(y, 0.0), 1.0), R_BASE * (1.0 + R_THINNESS / (1 + n_fired))
