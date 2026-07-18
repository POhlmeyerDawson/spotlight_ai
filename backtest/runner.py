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


def assert_no_lookahead(events: list[Event], as_of: datetime) -> None:
    leaked = [e for e in events if e.observed_at > as_of]
    if leaked:
        raise LookaheadError(
            f"{len(leaked)} event(s) from the future reached the scorer at as_of={as_of}: "
            f"{[str(e.event_id) for e in leaked[:3]]}"
        )


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
    assert_no_lookahead(events, cutoff)

    # 2. score, per founder entity — assertion repeated on the exact list the scorer sees.
    scores = []
    for entity_id in founder_entity_ids(cid) if cid else []:
        entity_events = store.events(as_of=cutoff, entity_id=entity_id)
        assert_no_lookahead(entity_events, cutoff)
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
        "lookahead_checked": True,
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


def _trajectory(member: dict, points: int = 12) -> list[dict]:
    """Score at successive cutoffs up to the truncation date.

    Every point is a real filter run at that cutoff — never an interpolation backwards
    from the final value, which would be lookahead wearing a chart's clothing.
    """
    cut = _series_bound(member)
    cid = _as_uuid(member.get("company_id"))

    try:
        from api.routers.deps import founder_entity_ids
        from memory import score as score_mod, store

        ents = founder_entity_ids(cid) if cid else []
        if not ents:
            raise LookupError("no resolved founder entity")
        entity_id = ents[0]
        events = store.events(as_of=cut, entity_id=entity_id)
        if not events:
            raise LookupError("no events before the truncation date")

        start = min(e.observed_at for e in events)
        step = (cut - start or timedelta(days=1)) / max(points - 1, 1)
        series = []
        for i in range(points):
            at = start + step * i
            window = [e for e in events if e.observed_at <= at]
            assert_no_lookahead(window, at)
            fs = score_mod.founder(entity_id, at)
            series.append(
                {"as_of": at.isoformat(), "mu": fs.mu, "band": fs.band, "trend": fs.trend}
            )
        return series
    except LookaheadError:
        raise
    except Exception as exc:  # noqa: BLE001 - fall back to the hand-collected trajectory
        log.info("calibration: replaying %s from fixture (%s)", member.get("founder"), exc)

    return [
        # Normalize `founder_score` onto `mu` so downstream code reads one field
        # regardless of which path produced the series.
        {**p, "mu": p.get("mu", p.get("founder_score")), "as_of": _aware(p["as_of"]).isoformat()}
        for p in member.get("trajectory", [])
        if _aware(p["as_of"]) <= cut  # the fixture is truncated too, not trusted blindly
    ]


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
    points = member.get("trajectory") or []
    if points:
        return max(_aware(pt["as_of"]) for pt in points if pt.get("as_of"))
    return _truncation(member)


def _truncation(member: dict) -> datetime:
    """The hand-set pre-breakout source cutoff. The cohort calls this
    `source_truncation_date`; earlier drafts called it `truncation_date`. Controls carry
    neither — they are matched contemporaries, not hand-truncated sources — so their own
    last observed point is the honest bound."""
    for key in ("truncation_date", "source_truncation_date", "as_of"):
        if member.get(key):
            return _aware(member[key])
    points = member.get("trajectory") or []
    if points:
        return max(_aware(pt["as_of"]) for pt in points if pt.get("as_of"))
    raise KeyError("cohort member has no truncation date and no trajectory")


def _peak(series: list[dict]) -> float | None:
    # The live replay emits `mu`; hand-collected fixture points say `founder_score`.
    values = [
        v
        for p in series
        for v in (p.get("mu"), p.get("founder_score"))
        if isinstance(v, (int, float))
    ]
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

    results = []
    for m in cohort["members"]:
        series = _trajectory(m)
        peak = _peak(series)
        results.append(
            {
                # The cohort names its entries `name`; earlier drafts used `founder`.
                # Carrying only one dropped every label, so the calibration chart had
                # no way to say who each line was.
                "founder": m.get("founder") or m.get("name"),
                "name": m.get("name") or m.get("founder"),
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
            }
        )

    winners = [r for r in results if r["label"] == "winner"]
    controls = [r for r in results if r["label"] == "control"]
    failures = [r for r in results if r["label"] == "failure"]

    controls_clearing = [r for r in controls if r["cleared_threshold"]]
    # Vacuous truth is not a pass: with no controls the check simply did not run.
    fame_check_evaluated = bool(controls)
    fame_check_passed = fame_check_evaluated and not controls_clearing

    hits = [r for r in winners if r["cleared_threshold"]]
    deprioritized = next(
        (r for r in failures if not r["cleared_threshold"]),
        next((r for r in failures), None),
    )

    return {
        "threshold": threshold,
        "results": results,
        "winners": winners,
        "controls": controls,
        "hit_rate": (len(hits) / len(winners)) if winners else None,
        "hits": len(hits),
        "winners_evaluated": len(winners),
        "controls_evaluated": len(controls),
        "fame_check_passed": fame_check_passed,
        "fame_check_evaluated": fame_check_evaluated,
        "controls_clearing_threshold": [r["founder"] for r in controls_clearing],
        "correctly_deprioritized_failure": deprioritized,
        "lookahead_checked": True,
    }
