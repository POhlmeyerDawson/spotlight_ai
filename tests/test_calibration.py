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
