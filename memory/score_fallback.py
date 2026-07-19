"""Beta-Binomial fallback behind ``SCORE_MODEL=beta_binomial``."""

from __future__ import annotations

import math
import os
from datetime import datetime
from uuid import UUID

from schema.events import FounderScore


def _lambda() -> float:
    return float(os.getenv("SCORE_LAMBDA", "0.985"))


def founder(entity_id: UUID, as_of: datetime) -> FounderScore:
    from memory.score import build_observations

    observations = build_observations(entity_id, as_of)
    retention = _lambda()
    alpha = beta = 1.0
    contributing: list[UUID] = []
    last_t: datetime | None = None
    before_last = after_last = 0.5

    for observation in observations:
        if last_t is not None:
            decay = retention ** ((observation.observed_at - last_t).total_seconds() / 86400.0)
            alpha = 1.0 + (alpha - 1.0) * decay
            beta = 1.0 + (beta - 1.0) * decay
        weight = observation.self_consistency / max(observation.source_penalty, 1e-6)
        before_last = alpha / (alpha + beta)
        alpha += weight * observation.value
        beta += weight * (1.0 - observation.value)
        after_last = alpha / (alpha + beta)
        contributing.extend(observation.event_ids)
        last_t = observation.observed_at

    if last_t is not None:
        elapsed = (as_of - last_t).total_seconds() / 86400.0
        if elapsed > 0:
            decay = retention**elapsed
            alpha = 1.0 + (alpha - 1.0) * decay
            beta = 1.0 + (beta - 1.0) * decay

    mu = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
    return FounderScore(
        entity_id=entity_id,
        as_of=as_of,
        mu=float(mu),
        band=float(math.sqrt(max(variance, 0.0))),
        trend=float(after_last - before_last),
        contributing_event_ids=contributing,
        model="beta_binomial",
    )
