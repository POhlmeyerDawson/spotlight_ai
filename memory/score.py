"""Founder Score: local-linear-trend Kalman filter. Owner: A. See A.md H8-12.

    x_t = [mu, nu]        mu = capability level, nu = momentum
    F   = [[1, dt], [0, 1]]
    Score = mu   Band = sqrt(P[0,0])   Trend = nu (structural, never a diff of scores)

Contradicted claims must never become observations — filter at the boundary here.
"""

from __future__ import annotations

import numpy as np
from datetime import datetime
from uuid import UUID

from schema.events import FounderScore

from memory.store import events


def founder(entity_id: UUID, as_of: datetime) -> FounderScore:
    """Compute founder score for an entity at a given time.

    Implements a simple local-linear-trend Kalman filter:
    - State: [mu, nu] where mu = capability, nu = momentum
    - Transition: x_t = F @ x_{t-1} where F = [[1, dt], [0, 1]]
    - Observations: confidence scores from events
    - Output: mu (score), sqrt(P[0,0]) (band/uncertainty), nu (trend)

    Args:
        entity_id: The entity to score
        as_of: The time to compute the score at

    Returns:
        FounderScore with mu, band, trend, and contributing event IDs
    """
    # Get all events for this entity up to as_of
    entity_events = events(as_of=as_of, entity_id=entity_id)

    if not entity_events:
        # Default score for unknown entities
        return FounderScore(
            entity_id=entity_id,
            as_of=as_of,
            mu=0.0,
            band=1.0,
            trend=0.0,
            contributing_event_ids=[],
            model="default",
        )

    # Sort events by observed_at
    entity_events.sort(key=lambda e: e.observed_at)

    # Initialize state: [mu, nu] and covariance P
    x = np.array([0.5, 0.0])  # Initial mu=0.5, nu=0
    P = np.array([[1.0, 0.0], [0.0, 1.0]])  # High initial uncertainty

    # Process noise
    Q = np.array([[0.01, 0.0], [0.0, 0.01]])

    # Observation noise (from confidence scores)
    R_base = 0.1

    # Process events in sequence
    for event in entity_events:
        dt = 1.0  # Simple discrete time step

        # Transition matrix
        F = np.array([[1.0, dt], [0.0, 1.0]])

        # Predict
        x = F @ x
        P = F @ P @ F.T + Q

        # Observation (confidence score)
        z = np.array([event.confidence])

        # Observation matrix (observe mu only)
        H = np.array([[1.0, 0.0]])

        # Innovation
        y = z - H @ x

        # Innovation covariance
        S = H @ P @ H.T + R_base

        # Kalman gain
        K = P @ H.T / S

        # Update
        x = x + K.flatten() * y[0]
        P = (np.eye(2) - K @ H) @ P

    # Final score
    mu = float(x[0])
    band = float(np.sqrt(P[0, 0]))
    trend = float(x[1])

    # Clip values to reasonable ranges
    mu = max(0.0, min(1.0, mu))
    band = max(0.01, min(1.0, band))
    trend = max(-1.0, min(1.0, trend))

    return FounderScore(
        entity_id=entity_id,
        as_of=as_of,
        mu=mu,
        band=band,
        trend=trend,
        contributing_event_ids=[e.event_id for e in entity_events],
        model="kalman",
    )


def forecast(entity_id: UUID, as_of: datetime, k_days: int) -> tuple[float, float]:
    """k-step prediction interval — propagate P forward. Falls out of the filter.

    Args:
        entity_id: The entity to forecast
        as_of: The current time
        k_days: Number of days to forecast

    Returns:
        Tuple of (predicted_mu, prediction_band)
    """
    score = founder(entity_id, as_of)

    # Simple forecast: extend the trend
    predicted_mu = score.mu + score.trend * k_days / 30  # Linear extrapolation
    predicted_band = score.band * np.sqrt(1 + k_days / 30)  # Uncertainty grows

    return max(0.0, min(1.0, predicted_mu)), predicted_band
