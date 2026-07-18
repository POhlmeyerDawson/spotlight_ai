"""Beta-Binomial with forgetting factor lambda. Owner: A.

Wired behind SCORE_MODEL=beta_binomial. Verify the flag works at H10, not H20.
Same observations as the Kalman (reused from score.py) — only the estimator differs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import numpy as np

from memory.score import R0, Observation, observations
from schema.events import FounderScore

LAMBDA = 0.97  # per-day forgetting: evidence half-life ~23 days
ALPHA0 = 1.0  # Beta(1,1) prior — uniform, mean 0.5, matching the Kalman's MU0
BETA0 = 1.0
W_MAX = 20.0  # a single very-low-noise proof event must not swamp the posterior


def _weight(o: Observation) -> float:
    """Precision weight, same noise model as the Kalman: low r == more pseudo-counts."""
    return float(min(R0 / max(o.r, 1e-9), W_MAX))


def _posterior(obs: list[Observation]) -> tuple[float, float]:
    a, b = ALPHA0, BETA0
    prev: datetime | None = None
    for o in obs:
        if prev is not None:
            decay = LAMBDA ** ((o.observed_at - prev).total_seconds() / 86400.0)
            a, b = ALPHA0 + (a - ALPHA0) * decay, BETA0 + (b - BETA0) * decay
        w = _weight(o)
        a += w * o.y
        b += w * (1.0 - o.y)
        prev = o.observed_at
    return a, b


def _mean(obs: list[Observation]) -> float:
    a, b = _posterior(obs)
    return a / (a + b)


def founder(entity_id: UUID, as_of: datetime) -> FounderScore:
    obs = observations(entity_id, as_of)
    kept = obs.kept
    a, b = _posterior(kept)
    n = a + b
    mu = a / n

    # Trend as a diff of window posterior means. Acceptable here; the Kalman's nu
    # is the principled version and this path only exists as a demo-time escape hatch.
    trend = 0.0
    if len(kept) >= 2:
        mid = len(kept) // 2
        trend = _mean(kept[mid:]) - _mean(kept[:mid])

    return FounderScore(
        entity_id=entity_id,
        as_of=as_of,
        mu=float(mu),
        band=float(np.sqrt(mu * (1.0 - mu) / (n + 1.0))),
        trend=float(trend),
        contributing_event_ids=[o.event_id for o in kept],
        model="beta_binomial",
    )
