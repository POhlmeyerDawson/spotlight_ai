"""Offline component-calibration report tests.

These tests validate the report shape and no-lookahead behavior of the Founder
Score component. They are not a claim of historical predictive performance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory import calibration, store
from schema.events import Event, EventKind, Source

T1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=30)
T3 = T2 + timedelta(days=30)


def _entity(name: str):
    return store.get_store().create_entity(name, name.casefold()).entity_id


def _event(entity_id, observed_at, *, kind=EventKind.GREEN_FLAG, source=Source.MANUAL, value=0.7):
    return Event(
        entity_id=entity_id,
        kind=kind,
        source=source,
        observed_at=observed_at,
        payload={"value": value, "self_consistency": 0.9},
    )


def test_calibration_report_contains_history_observations_trajectory_and_metrics() -> None:
    winner = _entity("Winner")
    control = _entity("Control")
    future = store.append(_event(winner, T3, value=0.99))
    observed = store.append(
        _event(winner, T1, kind=EventKind.PROOF_ARTIFACT, source=Source.PROOF_PROTOCOL, value=0.9)
    )
    store.append(_event(control, T1, source=Source.WEB, value=0.55))

    report = calibration.run_calibration(
        [winner, control],
        [T2, T1, T2],
        labels={winner: "winner", control: "control"},
        config=calibration.CalibrationConfig(threshold=0.6, sensitivity_step=0.1),
    )

    assert [point.as_of for point in report.results[0].trajectory] == [T1, T2]
    winner_result = next(result for result in report.results if result.entity_id == winner)
    assert winner_result.evaluation_timestamp == T2
    assert observed in [event.event_id for event in winner_result.raw_historical_events]
    assert future not in [event.event_id for event in winner_result.raw_historical_events]
    assert winner_result.derived_observations[0].event_ids == [observed]
    assert observed in winner_result.trajectory[0].derived_observation_event_ids
    assert winner_result.founder_score == winner_result.trajectory[-1].founder_score
    assert report.metrics.winner_hit_rate == 1.0
    assert report.metrics.control_false_positive_rate == 0.0
    assert report.metrics.separation_margin is not None and report.metrics.separation_margin > 0
    assert len(report.metrics.threshold_sensitivity) == 3
    assert report.config.threshold == 0.6

    payload = report.as_dict()
    assert payload["results"][0]["entity_id"] == str(report.results[0].entity_id)
    assert payload["results"][0]["raw_historical_events"]


def test_calibration_keeps_unknown_entities_and_empty_histories_explicit() -> None:
    empty = _entity("Unknown")
    report = calibration.run_calibration([empty], [T1])
    result = report.results[0]
    assert result.label == "unknown"
    assert result.raw_historical_events == []
    assert result.derived_observations == []
    assert result.founder_score.contributing_event_ids == []
    assert report.metrics.winner_hit_rate is None
    assert report.metrics.control_false_positive_rate is None
    assert report.metrics.separation_margin is None


def test_calibration_rejects_naive_cutoffs_and_unknown_labels() -> None:
    entity_id = _entity("Checked")
    with pytest.raises(ValueError, match="timezone-aware"):
        calibration.run_calibration([entity_id], [datetime(2024, 1, 1)])
    with pytest.raises(ValueError, match="unsupported calibration label"):
        calibration.run_calibration([entity_id], [T1], labels={entity_id: "maybe"})


# --- per-entity cutoffs -----------------------------------------------------
#
# A historical cohort has no shared calendar. These tests pin the behaviour that makes
# the harness usable for one: each entity is evaluated on its own timeline.


def test_per_entity_cutoffs_evaluate_each_entity_on_its_own_timeline() -> None:
    """One shared grid would score an early member years past its cutoff — lookahead —
    and a late member before it had any history, reporting the prior as a reading."""
    early = _entity("Early")
    late = _entity("Late")
    store.append(_event(early, T1, kind=EventKind.PROOF_ARTIFACT, source=Source.PROOF_PROTOCOL))
    store.append(_event(late, T3, kind=EventKind.PROOF_ARTIFACT, source=Source.PROOF_PROTOCOL))

    report = calibration.run_calibration(
        [early, late],
        {early: [T1, T2], late: [T2, T3]},
        labels={early: "winner", late: "control"},
    )

    by_id = {result.entity_id: result for result in report.results}
    assert [p.as_of for p in by_id[early].trajectory] == [T1, T2]
    assert [p.as_of for p in by_id[late].trajectory] == [T2, T3]
    # Each member's reported score is at ITS final cutoff, not a shared one.
    assert by_id[early].evaluation_timestamp == T2
    assert by_id[late].evaluation_timestamp == T3


def test_per_entity_cutoffs_refuse_an_entity_with_no_grid() -> None:
    """Silently falling back to a shared grid is how a member gets scored on the wrong
    calendar without anything saying so."""
    a, b = _entity("A"), _entity("B")
    with pytest.raises(ValueError, match="missing"):
        calibration.run_calibration([a, b], {a: [T1, T2]})


def test_a_shared_grid_still_works_unchanged() -> None:
    a, b = _entity("A"), _entity("B")
    report = calibration.run_calibration([a, b], [T2, T1])
    assert all([p.as_of for p in r.trajectory] == [T1, T2] for r in report.results)


# --- the cohort feed --------------------------------------------------------


def test_the_harness_reports_none_until_something_supplies_labels() -> None:
    """The state this module sat in: every metric defined, none of them computed,
    because no caller ever passed a label."""
    unlabelled = _entity("Unlabelled")
    store.append(_event(unlabelled, T1, kind=EventKind.PROOF_ARTIFACT, source=Source.PROOF_PROTOCOL))
    report = calibration.run_calibration([unlabelled], [T1])

    assert report.metrics.winner_hit_rate is None
    assert report.metrics.separation_margin is None
    assert report.metrics.winners_evaluated == 0


def test_component_run_reports_when_the_cohort_is_not_seeded() -> None:
    """No cohort in the store is a stated reason, never an empty-looking pass."""
    from backtest import component

    result = component.run()
    assert result["evaluated"] is False
    assert "seed" in result["reason"]


def test_component_maps_the_failure_member_onto_a_control_and_says_so() -> None:
    """The harness has no `failure` label. Folding it into `control` is correct for a
    threshold metric and wrong to do silently, so the mapping is reported."""
    from backtest import component

    assert component.LABEL_MAP["failure"] == "control"
    result = component.run()
    assert result["label_mapping"]["failure"] == "control"
