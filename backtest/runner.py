"""Time-machine backtest. Owner: D, with A + C. Proof #1 of the whole pitch.

Replays truncated historical sources through the SAME code path as live, with as_of
pinned before the founder was known. If it needs a special mode, it isn't a backtest —
so replay() calls the same score / screen / gate / memo functions the API calls, and
there is no `backtest=True` flag anywhere in this file.

assert_no_lookahead() is what makes the claim credible rather than merely asserted. It
runs before every scoring step and it raises. It never warns.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from backtest import collect
from schema.events import Event

log = logging.getLogger(__name__)


class LookaheadError(AssertionError):
    """Raised loudly. Never caught, never downgraded to a warning."""


def assert_no_lookahead(events: list[Event], as_of: datetime) -> int:
    """Raise if any event postdates as_of. Returns how many events were checked.

    The return value is not decoration. `lookahead_checked: True` used to be a
    literal in the report — a claim that the assertion had run, written by hand,
    in the one artifact whose entire job is proving the system does not fool
    itself. Callers now count what this function actually saw, so the report says
    "checked N events" or says it did not run. A hardcoded True is worse than no
    field at all.
    """
    leaked = [e for e in events if e.observed_at > as_of]
    if leaked:
        raise LookaheadError(
            f"{len(leaked)} event(s) from the future reached the scorer at as_of={as_of}: "
            f"{[str(e.event_id) for e in leaked[:3]]}"
        )
    return len(events)


def _aware(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(v))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_uuid(v: Any) -> UUID | None:
    if v is None or isinstance(v, UUID):
        return v
    try:
        return UUID(str(v))
    except (ValueError, TypeError):
        return None


def replay(company_id: Any, as_of: datetime) -> dict:
    """ingest -> score -> screen -> gate -> memo, at a cutoff before the founder was known.

    Identical call sequence to the live API path. Every stage that can be unimplemented
    degrades to None, but the LOOKAHEAD ASSERTION never degrades.
    """
    from api import memo as memo_mod
    from api.routers.deps import founder_entity_ids
    from memory import score as score_mod, store

    cutoff = _aware(as_of)
    cid = _as_uuid(company_id)

    # 1. ingest (read side): the as_of-scoped event set, checked before it goes anywhere.
    events = store.events(as_of=cutoff, company_id=cid)
    events_checked = assert_no_lookahead(events, cutoff)

    # 2. score, per founder entity — assertion repeated on the exact list the scorer sees.
    scores = []
    for entity_id in founder_entity_ids(cid) if cid else []:
        entity_events = store.events(as_of=cutoff, entity_id=entity_id)
        events_checked += assert_no_lookahead(entity_events, cutoff)
        fs = score_mod.founder(entity_id, cutoff)
        scores.append(
            {
                "entity_id": str(entity_id),
                "mu": fs.mu,
                "band": fs.band,
                "trend": fs.trend,
                "contributing_event_ids": [str(i) for i in fs.contributing_event_ids],
            }
        )

    # 3-4. screen + gate.
    screening = _stage(lambda: _screen(cid, cutoff), "screen")
    gate = _stage(lambda: _gate(cid, cutoff), "gate")

    # 5. memo — the same generator the API serves.
    memo = _stage(lambda: memo_mod.generate_memo(cid, cutoff), "memo")

    return {
        "company_id": str(company_id),
        "as_of": cutoff.isoformat(),
        "event_count": len(events),
        "scores": scores,
        "screening": screening,
        "gate": gate,
        "memo": memo,
        # Reported because it happened, not because it is expected to: the assertion
        # above ran over exactly this many events and did not raise.
        "lookahead_checked": True,
        "lookahead_events_checked": events_checked,
    }


def _stage(fn, name: str) -> Any:
    try:
        return fn()
    except LookaheadError:
        raise  # never swallowed — this is the one exception that must escape
    except Exception as exc:  # noqa: BLE001 - a stage still in progress must not stop the replay
        log.info("replay: stage %s unavailable (%s)", name, exc)
        return None


def _screen(cid: UUID | None, cutoff: datetime) -> dict | None:
    from intelligence import screen

    r = screen.three_axis(cid, cutoff)
    return {
        axis: {
            "score": getattr(r, axis).score,
            "trend": getattr(r, axis).trend,
            "confidence": getattr(r, axis).confidence,
            "evidence_event_ids": [str(i) for i in getattr(r, axis).evidence_event_ids],
        }
        for axis in ("founder", "market", "idea_vs_market")
    }


def _gate(cid: UUID | None, cutoff: datetime) -> dict:
    from intelligence import gate

    d = gate.evaluate(cid, cutoff)
    return {
        "outcome": str(d.outcome),
        "rationale": d.rationale,
        "absence_is_suspicious": d.absence_is_suspicious,
    }


# ---------------------------------------------------------------------------
# Calibration — the H12 gate and the most persuasive slide in the deck.
# ---------------------------------------------------------------------------


def _trajectory(member: dict, points: int = 12) -> tuple[list[dict], dict]:
    """Score at successive cutoffs up to the series bound. Returns (series, diagnostics).

    Every point is a real filter run at that cutoff — never an interpolation backwards
    from the final value, which would be lookahead wearing a chart's clothing.

    There is deliberately NO fixture fallback. This function used to catch LookupError
    and return the hand-authored trajectory from data/seed/backtest.json instead; every
    cohort member had a null company_id, so every member took that path, score.founder()
    was never called, and the report still described itself as a replay. A backtest that
    silently substitutes authored numbers for a failed replay is not degraded — it is
    false. When the replay cannot run, this returns an empty series and says why, and
    the member is counted as not replayed.
    """
    from api.routers.deps import founder_entity_ids
    from memory import score as score_mod, store

    cut = _series_bound(member)
    cid = _as_uuid(member.get("company_id"))
    diag = {"replayed": False, "events_checked": 0, "reason": None}

    if cid is None:
        diag["reason"] = "cohort member has no company_id in the store — run scripts/seed.py"
        return [], diag
    ents = founder_entity_ids(cid)
    if not ents:
        diag["reason"] = f"no founder entity resolves for company {cid}"
        return [], diag

    entity_id = ents[0]
    events = store.events(as_of=cut, entity_id=entity_id)
    if not events:
        diag["reason"] = f"no events on or before {cut.isoformat()}"
        return [], diag

    start = min(e.observed_at for e in events)
    step = (cut - start or timedelta(days=1)) / max(points - 1, 1)
    series = []
    checked = 0
    for i in range(points):
        at = start + step * i
        window = [e for e in events if e.observed_at <= at]
        checked += assert_no_lookahead(window, at)
        fs = score_mod.founder(entity_id, at)
        series.append(
            {
                "as_of": at.isoformat(),
                "mu": fs.mu,
                "band": fs.band,
                "trend": fs.trend,
                # How many events the filter actually consumed at this cutoff. Zero
                # means the point IS the prior (mu=0.5), not a reading — see _peak.
                "n_observations": len(fs.contributing_event_ids),
            }
        )

    diag.update({"replayed": True, "events_checked": checked, "entity_id": str(entity_id)})
    return series, diag


def _series_bound(member: dict) -> datetime:
    """How far a replayed trajectory may run — up to, but never through, breakout.

    Distinct from the source truncation date, and conflating the two was a bug:
    clipping the series at `source_truncation_date` (the FIRST point) collapsed every
    winner to a single value, so nothing cleared the threshold and the hit rate was 0.
    The claim under test is "the score was already rising before the breakout", which
    needs the span between those dates, not a point.
    """
    if member.get("breakout_at"):
        return _aware(member["breakout_at"])
    return _truncation(member)


def _truncation(member: dict) -> datetime:
    """The collection cutoff: the date past which no source was gathered for this member.

    The cohort calls this `collection_cutoff` and sets it to the member's breakout date,
    so every collected event predates the moment the founder became widely known.
    Earlier drafts used `truncation_date` / `source_truncation_date`; both are still
    accepted so a member written in the old shape does not silently lose its bound.
    """
    for key in ("collection_cutoff", "truncation_date", "source_truncation_date", "as_of"):
        if member.get(key):
            return _aware(member[key])
    raise KeyError("cohort member has no collection cutoff")


def _scored(series: list[dict]) -> list[dict]:
    """Points where the filter actually consumed evidence.

    The first cutoff of every series lands on the founder's earliest event, before any
    observation has been derived from it, so the filter returns the untouched prior of
    mu=0.5. That is the scorer saying "I know nothing", and it is not a score. Counting
    it made every control peak at exactly 0.5 — a flat 0.5 for a founder whose real
    replayed level is 0.22, and only 0.12 below the threshold the fame check turns on.
    A prior must never be reported as a measurement.
    """
    return [p for p in series if p.get("n_observations")]


def _peak(series: list[dict]) -> float | None:
    values = [p["mu"] for p in _scored(series) if isinstance(p.get("mu"), (int, float))]
    return max(values) if values else None


def run_calibration() -> dict:
    """Winners' trajectories vs controls, threshold line, hit rate, and one failure the
    system correctly deprioritized.

    fame_check_passed is the H12 hard gate: if a control clears the threshold, the score
    is measuring fame rather than trajectory and the thesis is dead. It is surfaced as a
    top-level boolean so it is impossible to miss or to quietly ignore.
    """
    cohort = collect.load_cohort()
    threshold = cohort["threshold"]
    by_id = {str(m.get("id")): m for m in cohort["members"] if m.get("id")}

    results = []
    events_checked = 0
    for m in cohort["members"]:
        series, diag = _trajectory(m)
        events_checked += diag["events_checked"]
        peak = _peak(series)
        results.append(
            {
                "replayed": diag["replayed"],
                "not_replayed_reason": diag["reason"],
                "lookahead_events_checked": diag["events_checked"],
                "detected_at": _first_clearing(series, threshold),
                # The cohort names its entries `name` and carries the founder as an
                # object so the seeder can mint a real entity from it. Carrying only
                # one of the two dropped every label, so the calibration chart had no
                # way to say who each line was.
                "founder": _founder_name(m),
                "name": m.get("name") or _founder_name(m),
                "id": m.get("id"),
                "sector": m.get("sector"),
                "outcome": m.get("outcome")
                or m.get("what_happened")
                or ("breakout" if m.get("label") == "winner" else None),
                "why": m.get("why_we_deprioritized") or m.get("truncation_note"),
                "company_id": m.get("company_id"),
                "label": str(m.get("label", "unknown")).lower(),
                "truncation_date": _truncation(m).isoformat(),
                "trajectory": series,
                "peak_mu": peak,
                "cleared_threshold": bool(peak is not None and peak >= threshold),
                "note": m.get("note"),
                # Whether this member's evidence is a real record or a composition is a
                # property of the EVIDENCE, and every metric computed downstream is only
                # as strong as it. It travels with the result rather than living in a
                # footnote nobody joins back.
                "control_kind": m.get("control_kind"),
                "evidence_provenance": m.get("evidence_provenance"),
                "evidence_parity": _evidence_parity(m, by_id) if m.get("matched_to") else None,
            }
        )

    winners = [r for r in results if r["label"] == "winner"]
    controls = [r for r in results if r["label"] == "control"]
    failures = [r for r in results if r["label"] == "failure"]

    # Only replayed members are evidence. A member whose replay did not run has no
    # score, and counting it as "did not clear" would turn a broken rig into a passing
    # fame check — the exact inversion this gate exists to catch.
    replayed_winners = [r for r in winners if r["replayed"]]
    replayed_controls = [r for r in controls if r["replayed"]]

    controls_clearing = [r for r in replayed_controls if r["cleared_threshold"]]
    # Vacuous truth is not a pass: with no REPLAYED controls the check did not run.
    fame_check_evaluated = bool(replayed_controls)
    fame_check_passed = fame_check_evaluated and not controls_clearing

    hits = [r for r in replayed_winners if r["cleared_threshold"]]
    deprioritized = next(
        (r for r in failures if r["replayed"] and not r["cleared_threshold"]),
        next((r for r in failures), None),
    )

    return {
        "threshold": threshold,
        "results": results,
        "winners": winners,
        "controls": controls,
        "hit_rate": (len(hits) / len(replayed_winners)) if replayed_winners else None,
        "hits": len(hits),
        "winners_evaluated": len(replayed_winners),
        "controls_evaluated": len(replayed_controls),
        "members_replayed": sum(1 for r in results if r["replayed"]),
        "members_total": len(results),
        "not_replayed": [
            {"name": r["name"], "reason": r["not_replayed_reason"]}
            for r in results
            if not r["replayed"]
        ],
        "fame_check_passed": fame_check_passed,
        "fame_check_evaluated": fame_check_evaluated,
        # `fame_check_passed` above is the raw assertion: no replayed control cleared.
        # It is true, and on its own it is misleading, because it says nothing about
        # whether the controls were capable of clearing. This decomposes it by the kind
        # of control it rests on and states the strength of the resulting claim. A
        # consumer that reports the boolean without the strength is reporting a PASS the
        # evidence does not support.
        "fame_check": _fame_check(replayed_controls),
        "controls_clearing_threshold": [r["founder"] for r in controls_clearing],
        "correctly_deprioritized_failure": deprioritized,
        # Both of these are measured, never asserted. `lookahead_checked` is True only
        # because assert_no_lookahead ran over `events_checked` events and did not
        # raise; with nothing replayed, nothing was checked and it reports False.
        "lookahead_checked": events_checked > 0,
        "events_checked": events_checked,
    }


def _raw_events(member: dict) -> int:
    """In-window source events collected for a member — the evidence it was scored on.

    Derived green flags are excluded: they are the system's own readings, so counting
    them would measure how much the pipeline had run rather than how much was collected.
    """
    return len(member.get("events") or [])


def _evidence_parity(member: dict, by_id: dict[str, dict]) -> dict:
    """How much evidence a control carries relative to the winner it is matched against.

    This exists because of a confound that only appears once controls are REAL. A
    synthetic control can be written with as many events as its winner, so a low score
    means something. A real control's evidence is whatever could be retrieved from the
    public record a decade later, and what could not be retrieved — historical commit
    volumes, star counts, contributor counts, the bodies of old threads — is exactly the
    material several scoring rules read. A real control therefore starts with fewer
    chances to fire a rule, and some of its shortfall is our archive access rather than
    its trajectory. Reporting the low score without reporting that is how a rig gets to
    look like a result.
    """
    matched = by_id.get(str(member.get("matched_to") or ""))
    mine = _raw_events(member)
    theirs = _raw_events(matched) if matched else 0
    ratio = (mine / theirs) if theirs else None
    return {
        "raw_events": mine,
        "matched_winner": matched.get("name") if matched else None,
        "matched_winner_raw_events": theirs or None,
        "ratio": ratio,
        # Half the matched winner's evidence is the line at which a control's failure to
        # clear stops being informative. It is a stated convention, not a derived value,
        # and it is stated here so it can be argued with rather than discovered.
        "sufficient": bool(ratio is not None and ratio >= EVIDENCE_PARITY_FLOOR),
        "floor": EVIDENCE_PARITY_FLOOR,
    }


def _fame_check(controls: list[dict]) -> dict:
    """The H12 verdict, decomposed by what kind of control it actually rests on.

    H12 says: if a control clears the threshold, the score is measuring fame rather than
    trajectory. The verdict is only as good as the controls. Two independent weaknesses
    apply to the two kinds we have, and reporting a bare PASS over their union would
    launder both:

      SYNTHETIC controls were written by the same author as the winners they are
      compared against. The scorer separated them unaided, but the evidence it separated
      was composed by someone who already knew which side should win.

      REAL controls fix that — their outcomes are facts about the world, not choices —
      but only their retrievable evidence could be reconstructed, so they are scored on
      thinner input than their winners.

    Neither arm alone establishes the gate. The verdict is therefore reported per arm,
    with the union verdict marked for exactly what it is.
    """
    real = [c for c in controls if c.get("control_kind") == "real"]
    synthetic = [c for c in controls if c.get("control_kind") != "real"]
    sufficient = [c for c in real if (c.get("evidence_parity") or {}).get("sufficient")]

    def arm(members: list[dict], name: str) -> dict:
        clearing = [c for c in members if c["cleared_threshold"]]
        return {
            "arm": name,
            "evaluated": bool(members),
            "controls": [c["name"] for c in members],
            "n": len(members),
            "passed": bool(members) and not clearing,
            "clearing": [c["name"] for c in clearing],
        }

    real_arm = arm(sufficient, "real controls at sufficient evidence parity")
    synthetic_arm = arm(synthetic, "synthetic controls")
    underpowered = [c["name"] for c in real if c not in sufficient]

    if real_arm["evaluated"] and real_arm["passed"] and real_arm["n"] >= MIN_REAL_CONTROLS:
        strength = "moderate"
        reading = (
            f"No real contemporary cleared the threshold, and {real_arm['n']} of them carried "
            f"at least {int(EVIDENCE_PARITY_FLOOR * 100)}% of their matched winner's evidence. "
            f"This is the strongest arm available without archival API access, and it is still "
            f"not a clean pass: their unretrievable payload fields are inputs several scoring "
            f"rules read, so part of the gap is our archive access rather than their trajectory."
        )
    elif real_arm["evaluated"] and real_arm["passed"]:
        # One control that did not clear is one company's behaviour, not a gate. Calling
        # it a pass is the vacuous-truth failure this check already learned once: a
        # verdict that cannot fail is not a verdict.
        strength = "indeterminate"
        reading = (
            f"Only {real_arm['n']} real control ({', '.join(real_arm['controls'])}) carries "
            f"enough evidence to count, against a floor of {MIN_REAL_CONTROLS}. It did not "
            f"clear the threshold, but a gate that turns on one company is a fact about that "
            f"company. H12 is NOT established on real contemporaries; the synthetic arm below "
            f"passes and is a materially weaker test. Treat the union verdict as unproven."
        )
    elif real_arm["evaluated"]:
        strength = "failing"
        reading = (
            f"A real contemporary cleared the threshold: {real_arm['clearing']}. Under H12 that "
            f"means the score is measuring fame rather than trajectory, and feature work stops."
        )
    else:
        strength = "weak"
        reading = (
            "The verdict rests on synthetic controls only. Their author also wrote the winners, "
            "so the comparison cannot distinguish a scorer that detects trajectory from one that "
            "detects how the two sides were written. This is not a historical backtest result."
        )

    return {
        "strength": strength,
        "reading": reading,
        "real": real_arm,
        "synthetic": synthetic_arm,
        "real_controls_below_parity_floor": underpowered,
        "requirements_for_a_clean_verdict": REAL_BACKTEST_REQUIREMENTS,
    }


# What an H12 backtest would need to be genuinely historical, written down so the gap
# between what is claimed and what is established stays visible in the artifact itself.
REAL_BACKTEST_REQUIREMENTS: tuple[str, ...] = (
    "Real non-breakout contemporaries only. Synthetic controls test whether the scorer "
    "can separate two bodies of text written by one author, which is a different and "
    "much easier question than the one H12 asks.",
    "Archival source access at the control's own dates: commit counts, lines changed, "
    "contributor counts and star counts AS OF the historical cutoff, not today's values "
    "read backwards. The GitHub API serves current state; period-accurate figures need "
    "Wayback snapshots of the repository page or an archival dataset such as GH Archive.",
    "The full text of the historical threads, not just their scores. Several rules read "
    "what a founder wrote — whether they stated assumptions, defined non-goals, explained "
    "a trade-off — and a control reconstructed from titles and point counts alone cannot "
    "fire them at all, so it is handicapped on precisely the axes the product claims to "
    "judge.",
    "Enough controls per winner that one member landing either way does not move the "
    "verdict. At three real controls, the gate turns on individual companies.",
    "Controls selected before their scores are known, by a rule stated in advance, so "
    "the selection cannot drift toward companies that happen to score low.",
)

# A control carrying less than this fraction of its matched winner's collected evidence
# is reported but does not count toward the H12 verdict.
EVIDENCE_PARITY_FLOOR = 0.5

# Fewest real controls at sufficient parity for the real arm to be a verdict rather than
# an anecdote. Two is not a sample either — it is the point below which the gate cannot
# fail for any reason except one company, which is what makes it worth stating.
MIN_REAL_CONTROLS = 2


def _founder_name(member: dict) -> str | None:
    founder = member.get("founder")
    if isinstance(founder, dict):
        return founder.get("display_name") or founder.get("name_normalized")
    return str(founder) if founder else member.get("name")


def _first_clearing(series: list[dict], threshold: float) -> str | None:
    """The earliest replayed as_of at which the founder axis cleared the threshold.

    This is the "we would have found them on this date" claim, and it is read off the
    replayed series rather than recorded in the fixture. The cohort file used to carry
    a `detected_at` per winner; a detection date that the replay did not produce is a
    prediction written after the fact.
    """
    for point in _scored(series):
        if isinstance(point.get("mu"), (int, float)) and point["mu"] >= threshold:
            return point["as_of"]
    return None
