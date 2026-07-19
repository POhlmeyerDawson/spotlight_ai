"""Feeds the backtest cohort into ``memory/calibration.py``. Owner: D, with A.

WHY THIS FILE EXISTS
--------------------
``memory/calibration.py`` is a complete, typed, no-lookahead calibration harness that
takes entity ids, cutoffs and winner/control labels. Nothing supplied it any. It has
threshold sensitivity, separation margin, hit rates and false-positive rates, and until
now every one of them returned ``None`` in production because no caller ever passed a
label. A harness with no feed is indistinguishable from a harness that does not work.

The wiring lives HERE and not there on purpose. That module's own docstring commits it
to not loading a cohort and not depending on D's fixtures, and that is the right
boundary: it is the component-level calibration primitive, reusable against any set of
entities. So the dependency points backtest -> memory, never the reverse.

WHAT THE HARNESS TURNED OUT TO NEED
-----------------------------------
One thing, and it was a genuine shape mismatch rather than a missing argument. The
harness applied a single cutoff grid to every entity, and a historical cohort has no
shared calendar: Docker's window closes in June 2014 and Supabase's opens in January
2020. A shared grid scores Supabase at 2014 — where it has no history, so the filter
returns its untouched prior of 0.5, which is the scorer saying "I know nothing" and is
not a reading — and scores Docker at 2021, seven years past the breakout its cutoff
exists to precede, which is lookahead. ``run_calibration`` now accepts per-entity
cutoffs, and this module supplies each member its own.

THE ONE LABEL THAT DOES NOT MAP CLEANLY, STATED RATHER THAN QUIETLY COERCED
---------------------------------------------------------------------------
The harness's vocabulary is winner / control / other / unknown. The cohort also carries
a ``failure``: a company the system correctly deprioritized. For the threshold metrics a
failure is a negative example exactly like a control — neither broke out — so it is
mapped to ``control`` and counted. That merge is reported in the result under
``label_mapping`` so nobody reads ``controls_evaluated`` as a count of matched controls.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from backtest import collect, runner

log = logging.getLogger(__name__)

# Cohort label -> the harness's vocabulary. `failure` folds into `control` because the
# metrics that consume it ask one question of a negative: did it clear the threshold.
LABEL_MAP = {"winner": "winner", "control": "control", "failure": "control"}

# Trajectory points per member, matching backtest/runner.py so the component report and
# the replay describe the same series rather than two differently-sampled ones.
POINTS = 12


def _entity_for(company_id: UUID) -> UUID | None:
    from api.routers.deps import founder_entity_ids

    entity_ids = founder_entity_ids(company_id)
    return entity_ids[0] if entity_ids else None


def _cutoffs(entity_id: UUID, bound: datetime, points: int = POINTS) -> list[datetime]:
    """The member's own grid: from its first observed event up to its collection bound.

    Identical construction to ``runner._trajectory`` — first event to bound, evenly
    spaced — so the two reports are two views of one replay, not two replays.
    """
    from memory import store

    events = store.events(entity_id=entity_id, as_of=bound)
    if not events:
        return []
    start = min(event.observed_at for event in events)
    span = bound - start
    if not span:
        return [bound]
    step = span / max(points - 1, 1)
    return [start + step * index for index in range(points)]


def cohort_inputs() -> dict[str, Any]:
    """Resolve the cohort to (entity ids, per-entity cutoffs, labels) for the harness."""
    cohort = collect.load_cohort()
    entity_ids: list[UUID] = []
    cutoffs: dict[UUID, list[datetime]] = {}
    labels: dict[UUID, str] = {}
    skipped: list[dict] = []

    for member in cohort["members"]:
        name = member.get("name")
        label = LABEL_MAP.get(str(member.get("label", "")).lower())
        company_id = runner._as_uuid(member.get("company_id"))
        if label is None:
            skipped.append({"name": name, "reason": f"unmapped label {member.get('label')!r}"})
            continue
        if company_id is None:
            skipped.append({"name": name, "reason": "no company_id in the store — run scripts/seed.py"})
            continue
        entity_id = _entity_for(company_id)
        if entity_id is None:
            skipped.append({"name": name, "reason": f"no founder entity resolves for {company_id}"})
            continue
        grid = _cutoffs(entity_id, runner._series_bound(member))
        if not grid:
            skipped.append({"name": name, "reason": "no events on or before the collection cutoff"})
            continue
        entity_ids.append(entity_id)
        cutoffs[entity_id] = grid
        labels[entity_id] = label

    return {
        "entity_ids": entity_ids,
        "cutoffs": cutoffs,
        "labels": labels,
        "skipped": skipped,
        "threshold": cohort["threshold"],
        "names": {
            _entity_for(runner._as_uuid(m["company_id"])): m.get("name")
            for m in cohort["members"]
            if runner._as_uuid(m.get("company_id")) is not None
        },
    }


def run() -> dict:
    """Run ``memory.calibration`` over the real cohort and return its report as a dict."""
    from memory import calibration

    inputs = cohort_inputs()
    if not inputs["entity_ids"]:
        return {
            "evaluated": False,
            "reason": "no cohort member resolved to a scoreable entity — run scripts/seed.py",
            "skipped": inputs["skipped"],
            "label_mapping": LABEL_MAP,
        }

    report = calibration.run_calibration(
        inputs["entity_ids"],
        inputs["cutoffs"],
        labels=inputs["labels"],
        config=calibration.CalibrationConfig(threshold=inputs["threshold"]),
    )
    metrics = report.metrics
    return {
        "evaluated": True,
        "label_mapping": LABEL_MAP,
        "label_mapping_note": (
            "The cohort's `failure` member is counted as a control: for a threshold "
            "metric it is a negative example like any other. `controls_evaluated` is "
            "therefore matched controls PLUS the deprioritized failure."
        ),
        "skipped": inputs["skipped"],
        "threshold": report.config.threshold,
        "winner_hit_rate": metrics.winner_hit_rate,
        "control_false_positive_rate": metrics.control_false_positive_rate,
        "separation_margin": metrics.separation_margin,
        "winners_evaluated": metrics.winners_evaluated,
        "controls_evaluated": metrics.controls_evaluated,
        "threshold_sensitivity": [
            {
                "threshold": metric.threshold,
                "winner_hit_rate": metric.winner_hit_rate,
                "control_false_positive_rate": metric.control_false_positive_rate,
            }
            for metric in metrics.threshold_sensitivity
        ],
        "per_member": [
            {
                "name": inputs["names"].get(result.entity_id),
                "entity_id": str(result.entity_id),
                "label": result.label,
                "final_as_of": result.evaluation_timestamp.isoformat(),
                "mu": result.founder_score.mu,
                "band": result.founder_score.band,
                "trajectory_points": len(result.trajectory),
                "observations": len(result.derived_observations),
            }
            for result in report.results
        ],
        # The harness scores each member at its FINAL cutoff, which for a winner is the
        # moment before breakout. That is the number the metrics above are computed on,
        # and it is deliberately not the peak the replay reports: peak-over-trajectory
        # answers "would we ever have flagged them", final-value answers "were they
        # flagged at the last moment we could still have acted". Both are stated because
        # they are different questions and only one of them is the H12 gate.
        "measured_at": "each member's final pre-breakout cutoff, not the trajectory peak",
    }


__all__ = ["LABEL_MAP", "POINTS", "cohort_inputs", "run"]
