"""Offline calibration reports for the Founder Score.

This module owns only the component-level calibration boundary. It takes explicit
entity IDs and explicit as_of cutoffs, reads the same store and scorer used by the
live path, and returns a typed report that an integrated backtest can consume later.
It does not load a cohort, call a network service, or depend on Builder D's fixtures.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Literal, Mapping, Sequence
from uuid import UUID

from pydantic import BaseModel, Field

from core.config import settings
from memory import score, store
from schema.events import Event, FounderScore, Observation

CalibrationLabel = Literal["winner", "control", "other", "unknown"]


class CalibrationConfig(BaseModel):
    """The threshold and scorer settings captured alongside a report."""

    threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    sensitivity_step: float = Field(default=0.05, ge=0.0, le=1.0)
    score_model: str = Field(default_factory=lambda: os.getenv("SCORE_MODEL", settings.score_model))
    score_q: float = Field(default_factory=lambda: score._params()[0])
    score_r0: float = Field(default_factory=lambda: score._params()[1])


class CalibrationPoint(BaseModel):
    """One no-lookahead score at one historical cutoff."""

    as_of: datetime
    raw_event_ids: list[UUID] = Field(default_factory=list)
    derived_observation_event_ids: list[UUID] = Field(default_factory=list)
    founder_score: FounderScore


class ThresholdMetric(BaseModel):
    threshold: float
    winner_hit_rate: float | None = None
    control_false_positive_rate: float | None = None


class CalibrationMetrics(BaseModel):
    """Metrics that are defined only over supplied winner/control labels."""

    winner_hit_rate: float | None = None
    control_false_positive_rate: float | None = None
    separation_margin: float | None = None
    threshold_sensitivity: list[ThresholdMetric] = Field(default_factory=list)
    winners_evaluated: int = 0
    controls_evaluated: int = 0


class CalibrationResult(BaseModel):
    """The complete result for one founder/entity."""

    entity_id: UUID
    evaluation_timestamp: datetime
    raw_historical_events: list[Event] = Field(default_factory=list)
    derived_observations: list[Observation] = Field(default_factory=list)
    founder_score: FounderScore
    trajectory: list[CalibrationPoint] = Field(default_factory=list)
    label: CalibrationLabel = "unknown"


class CalibrationReport(BaseModel):
    """A JSON-ready component calibration report."""

    config: CalibrationConfig
    results: list[CalibrationResult] = Field(default_factory=list)
    metrics: CalibrationMetrics

    def as_dict(self) -> dict:
        """Return a transport-friendly representation for a later backtest."""
        return self.model_dump(mode="json")


def run_calibration(
    entity_ids: Sequence[UUID | str],
    evaluation_times: Sequence[datetime],
    *,
    labels: Mapping[UUID | str, str | None] | None = None,
    config: CalibrationConfig | None = None,
) -> CalibrationReport:
    """Score explicit histories at explicit cutoffs, with no lookahead.

    ``evaluation_times`` are sorted and de-duplicated. Every trajectory point reads
    ``events(as_of=...)`` and calls ``score.founder(..., as_of=...)`` independently;
    it never derives earlier values by interpolating backward from a final score.
    """
    cutoffs = _ordered_cutoffs(evaluation_times)
    entities = list(dict.fromkeys(_as_uuid(entity_id) for entity_id in entity_ids))
    label_map = _labels(labels)
    report_config = config or CalibrationConfig()

    results: list[CalibrationResult] = []
    for entity_id in entities:
        trajectory: list[CalibrationPoint] = []
        for as_of in cutoffs:
            raw_events = store.get_store().events(entity_id=entity_id, as_of=as_of)
            observations = score.build_observations(entity_id, as_of)
            founder_score = score.founder(entity_id, as_of)
            trajectory.append(
                CalibrationPoint(
                    as_of=as_of,
                    raw_event_ids=[event.event_id for event in raw_events],
                    derived_observation_event_ids=[
                        event_id
                        for observation in observations
                        for event_id in observation.event_ids
                    ],
                    founder_score=founder_score,
                )
            )

        final = trajectory[-1]
        final_events = store.get_store().events(entity_id=entity_id, as_of=final.as_of)
        final_observations = score.build_observations(entity_id, final.as_of)
        results.append(
            CalibrationResult(
                entity_id=entity_id,
                evaluation_timestamp=final.as_of,
                raw_historical_events=final_events,
                derived_observations=final_observations,
                founder_score=final.founder_score,
                trajectory=trajectory,
                label=label_map.get(entity_id, "unknown"),
            )
        )

    metrics = _metrics(results, report_config)
    return CalibrationReport(config=report_config, results=results, metrics=metrics)


def _as_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid entity ID: {value!r}") from exc


def _ordered_cutoffs(values: Sequence[datetime]) -> list[datetime]:
    if not values:
        raise ValueError("at least one evaluation timestamp is required")
    if any(value.tzinfo is None for value in values):
        raise ValueError("evaluation timestamps must be timezone-aware")
    return sorted(set(values))


def _labels(values: Mapping[UUID | str, str | None] | None) -> dict[UUID, CalibrationLabel]:
    normalized: dict[UUID, CalibrationLabel] = {}
    for entity_id, label in (values or {}).items():
        value = "unknown" if label is None else str(label).strip().lower()
        if value not in {"winner", "control", "other", "unknown"}:
            raise ValueError(f"unsupported calibration label: {label!r}")
        normalized[_as_uuid(entity_id)] = value  # type: ignore[assignment]
    return normalized


def _metrics(results: Sequence[CalibrationResult], config: CalibrationConfig) -> CalibrationMetrics:
    winners = [r.founder_score.mu for r in results if r.label == "winner"]
    controls = [r.founder_score.mu for r in results if r.label == "control"]

    def hit_rate(values: Sequence[float], threshold: float) -> float | None:
        return (sum(value >= threshold for value in values) / len(values)) if values else None

    thresholds = {config.threshold}
    if config.sensitivity_step:
        thresholds.update(
            {
                max(0.0, config.threshold - config.sensitivity_step),
                min(1.0, config.threshold + config.sensitivity_step),
            }
        )
    threshold_metrics = [
        ThresholdMetric(
            threshold=threshold,
            winner_hit_rate=hit_rate(winners, threshold),
            control_false_positive_rate=hit_rate(controls, threshold),
        )
        for threshold in sorted(thresholds)
    ]
    separation = None
    if winners and controls:
        separation = sum(winners) / len(winners) - sum(controls) / len(controls)
    base = next(metric for metric in threshold_metrics if metric.threshold == config.threshold)
    return CalibrationMetrics(
        winner_hit_rate=base.winner_hit_rate,
        control_false_positive_rate=base.control_false_positive_rate,
        separation_margin=separation,
        threshold_sensitivity=threshold_metrics,
        winners_evaluated=len(winners),
        controls_evaluated=len(controls),
    )


__all__ = [
    "CalibrationConfig",
    "CalibrationMetrics",
    "CalibrationPoint",
    "CalibrationReport",
    "CalibrationResult",
    "ThresholdMetric",
    "run_calibration",
]
