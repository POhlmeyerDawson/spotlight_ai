"""Founder scoring with a calibrated observation boundary and Kalman filter.

Builder A's local-linear-trend filter remains authoritative. Builder C's green-flag
calibration, payload shapes, proof weighting, and contradiction references are
integrated at the event-to-observation boundary.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import numpy as np

from core.config import settings
from memory import queries, store
from schema.events import Event, EventKind, FounderScore, Observation as SchemaObservation, Source

log = logging.getLogger(__name__)

_OBSERVATION_KINDS = {
    EventKind.GREEN_FLAG,
    EventKind.PROOF_ARTIFACT,
    EventKind.PROOF_BEHAVIOR,
}
OBSERVATION_KINDS = tuple(_OBSERVATION_KINDS)

# A's source weighting remains the baseline. C's proof-kind multiplier makes
# verified behavioral evidence materially less noisy without rewarding raw volume.
_SOURCE_PENALTY = {
    Source.PROOF_PROTOCOL: 0.15,
    Source.VALIDATOR: 0.6,
    Source.GITHUB: 0.6,
    Source.ARXIV: 0.7,
    Source.MANUAL: 1.2,
    Source.HN: 0.9,
    Source.WEB: 1.0,
    Source.DECK: 2.0,
}
SOURCE_PENALTY = _SOURCE_PENALTY
KIND_NOISE = {
    EventKind.GREEN_FLAG: 1.0,
    EventKind.PROOF_ARTIFACT: 0.2,
    EventKind.PROOF_BEHAVIOR: 0.2,
}

MU0 = 0.5
R0 = 0.08
P0 = (0.25, 0.25)  # public compatibility constants for calibration diagnostics
_X0 = np.array([MU0, 0.0])
_P0 = np.array([[P0[0], 0.0], [0.0, P0[1]]])
H = np.array([[1.0, 0.0]])
_H = H
_DAYS_PER_YEAR = 365.25
_CLAIM_REF_KEYS = ("claim_id", "claim_ids", "supporting_claim_ids", "supporting_claims")

# Green-flag rates are sensor yes-rates, not capability scores. This monotone
# calibration puts them on the same scale as the other observations while retaining
# the raw event payload and shrinking unknown evidence counts toward the cohort prior.
RATE_MID = 0.30
RATE_SLOPE = 8.0
# Keep calibration on the capability scale without inflating a high raw signal;
# this preserves the anti-volume invariant for repeated evidence.
SCORE_FLOOR, SCORE_CEIL = 0.12, 0.80
RATE_PRIOR = 0.26
SHRINK_K = 8.0


@dataclass(frozen=True)
class Observation:
    """Compatibility view used by C diagnostics; A uses ``SchemaObservation``."""

    event_id: UUID
    observed_at: datetime
    y: float
    r: float


@dataclass(frozen=True)
class ObservationSet:
    kept: list[Observation]
    dropped_contradicted: list[UUID]


def _params() -> tuple[float, float]:
    return float(os.getenv("SCORE_Q", "0.01")), float(os.getenv("SCORE_R0", str(R0)))


def _dt_years(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 86400.0 / _DAYS_PER_YEAR


def _active_model() -> str:
    return os.getenv("SCORE_MODEL", settings.score_model)


def _as_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _claim_refs(payload: dict) -> set[UUID]:
    refs: set[UUID] = set()
    for key in _CLAIM_REF_KEYS:
        value = payload.get(key)
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            if isinstance(item, dict):
                item = item.get("claim_id")
            if parsed := _as_uuid(item):
                refs.add(parsed)
    return refs


def _verdict_entries(payload: dict) -> list[dict]:
    nested = payload.get("claims") or payload.get("verdicts")
    return (
        [entry for entry in nested if isinstance(entry, dict)]
        if isinstance(nested, list)
        else [payload]
    )


def contradicted_claim_ids(as_of: datetime) -> set[UUID]:
    out: set[UUID] = set()
    for event in store.events(as_of=as_of, kind=EventKind.VALIDATION_RESULT):
        for entry in _verdict_entries(event.payload):
            status = str(entry.get("status") or entry.get("verdict") or "").lower()
            if status.endswith("contradicted") and (claim_id := _as_uuid(entry.get("claim_id"))):
                out.add(claim_id)
    return out


def calibrate(rate: float, n_flags: int = 0) -> float:
    """Map a weighted green-flag yes-rate monotonically to capability scale."""
    try:
        count = int(n_flags)
    except (TypeError, ValueError):
        count = 0
    n = count if count > 0 else int(SHRINK_K)
    shrunk = (float(rate) * n + RATE_PRIOR * SHRINK_K) / (n + SHRINK_K)
    squashed = 1.0 / (1.0 + float(np.exp(-RATE_SLOPE * (shrunk - RATE_MID))))
    return float(SCORE_FLOOR + squashed * (SCORE_CEIL - SCORE_FLOOR))


def _flag_count(payload: dict) -> int:
    flags = payload.get("flags")
    if isinstance(flags, list):
        return len(flags)
    count = payload.get("n_flags") or payload.get("evaluated")
    return int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) else 0


def _derive_y(payload: dict) -> float | None:
    for key in ("value", "y", "yes_rate", "score"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(np.clip(value, 0.0, 1.0))
    flags = payload.get("flags")
    if isinstance(flags, list) and flags:
        numerator = denominator = 0.0
        for flag in flags:
            if not isinstance(flag, dict):
                continue
            weight = flag.get("weight", 1.0)
            weight = (
                float(weight)
                if isinstance(weight, (int, float)) and not isinstance(weight, bool)
                else 1.0
            )
            denominator += weight
            numerator += weight if bool(flag.get("fired")) else 0.0
        if denominator:
            return float(np.clip(numerator / denominator, 0.0, 1.0))
    # A per-rule RECEIPT is not an observation. Receipts carry `rule_id` and `fired`
    # so the trace can show which rules fired and on what evidence; the reading itself
    # lives on the single rollup. Scoring receipts too counts a founder once per rule
    # instead of once per evaluation — it narrows the band by the square root of the
    # rule count and lets how MANY rules exist outweigh how well the founder did.
    # Measured with this clause active: the adversarial burst outscored its own
    # legitimate control, 37.0 to 17.5, inverting the beat that exists to show we do
    # not false-positive fast builders.
    if "rule_id" in payload:
        return None
    if "fired" in payload:
        return 1.0 if payload["fired"] else 0.0
    return None


def _source_penalty(event: Event, payload: dict) -> float:
    value = payload.get("source_penalty", _SOURCE_PENALTY.get(event.source, 1.0))
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 1.0


def _kind_noise(event: Event) -> float:
    return KIND_NOISE.get(event.kind, 1.0)


def _noise(event: Event, payload: dict) -> float:
    consistency = payload.get("self_consistency", event.confidence)
    consistency = (
        float(consistency)
        if isinstance(consistency, (int, float)) and not isinstance(consistency, bool)
        else 1.0
    )
    consistency = float(np.clip(consistency, 0.05, 1.0))
    return max(
        _params()[1] / consistency * _source_penalty(event, payload) * _kind_noise(event), 1e-6
    )


def _observation_from_event(event: Event, entity_id: UUID) -> SchemaObservation | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    value = _derive_y(payload)
    if value is None:
        return None
    if event.kind == EventKind.GREEN_FLAG:
        value = calibrate(value, _flag_count(payload))
    consistency = payload.get("self_consistency", event.confidence)
    consistency = (
        float(consistency)
        if isinstance(consistency, (int, float)) and not isinstance(consistency, bool)
        else 1.0
    )
    consistency = float(np.clip(consistency, 0.05, 1.0))
    penalty = _source_penalty(event, payload) * _kind_noise(event)
    rule_ids = payload.get("rule_ids")
    if isinstance(rule_ids, (list, tuple)):
        normalized_rule_ids = [str(rule_id) for rule_id in rule_ids]
    elif rule_ids is None:
        normalized_rule_ids = [str(payload["rule_id"])] if "rule_id" in payload else []
    else:
        normalized_rule_ids = [str(rule_ids)]
    return SchemaObservation(
        entity_id=entity_id,
        observed_at=event.observed_at,
        value=value,
        self_consistency=consistency,
        source_penalty=penalty,
        event_ids=[event.event_id],
        rule_ids=normalized_rule_ids,
    )


def build_observations(entity_id: UUID, as_of: datetime) -> list[SchemaObservation]:
    contradicted_events = queries.contradicted_event_ids(entity_id, as_of)
    contradicted_claims = contradicted_claim_ids(as_of)
    observations_for_entity: list[SchemaObservation] = []
    for event in store.get_store().events(entity_id=entity_id, as_of=as_of):
        if event.kind not in _OBSERVATION_KINDS:
            continue
        if (
            event.event_id in contradicted_events
            or _claim_refs(event.payload) & contradicted_claims
        ):
            log.info(
                "score: dropping contradicted observation %s for %s", event.event_id, entity_id
            )
            continue
        if observation := _observation_from_event(event, entity_id):
            observations_for_entity.append(observation)
        else:
            log.debug("score: unrecognised payload shape on %s (%s)", event.event_id, event.kind)
    observations_for_entity.sort(key=lambda item: (item.observed_at, str(item.event_ids[0])))

    # Exact duplicate evidence at one world-time is one measurement, not N votes.
    # Keep all event IDs so the score remains auditable and receipts are lossless.
    deduplicated: list[SchemaObservation] = []
    for observation in observations_for_entity:
        if deduplicated:
            previous = deduplicated[-1]
            same_measurement = (
                previous.observed_at == observation.observed_at
                and previous.value == observation.value
                and previous.self_consistency == observation.self_consistency
                and previous.source_penalty == observation.source_penalty
                and previous.rule_ids == observation.rule_ids
            )
            if same_measurement:
                deduplicated[-1] = previous.model_copy(
                    update={"event_ids": [*previous.event_ids, *observation.event_ids]}
                )
                continue
        deduplicated.append(observation)
    return deduplicated


def observations(entity_id: UUID, as_of: datetime) -> ObservationSet:
    """C-compatible diagnostic view over A's shared observation boundary."""
    kept_schema = build_observations(entity_id, as_of)
    kept_ids = {event_id for item in kept_schema for event_id in item.event_ids}
    kept = [
        Observation(item.event_ids[0], item.observed_at, item.value, _noise_for_schema(item))
        for item in kept_schema
    ]
    dropped: list[UUID] = []
    for event in store.get_store().events(entity_id=entity_id, as_of=as_of):
        if event.kind in _OBSERVATION_KINDS and _derive_y(event.payload) is not None:
            if event.event_id not in kept_ids:
                dropped.append(event.event_id)
    return ObservationSet(kept, dropped)


def _noise_for_schema(observation: SchemaObservation) -> float:
    return max(_params()[1] / observation.self_consistency * observation.source_penalty, 1e-6)


def _F(dt_years: float) -> np.ndarray:
    return np.array([[1.0, dt_years], [0.0, 1.0]])


# Silence is not evidence. A trend measured from past readings must not be
# extrapolated indefinitely: momentum decays with a 90-day half-life, which bounds
# total drift to v0 / DECAY_RATE, and uncertainty never exceeds the no-evidence prior.
MOMENTUM_HALFLIFE_DAYS = 90.0
_DECAY_RATE = np.log(2.0) / (MOMENTUM_HALFLIFE_DAYS / _DAYS_PER_YEAR)


def _propagate_through_silence(
    x: np.ndarray, covariance: np.ndarray, dt_years: float, q: float
) -> tuple[np.ndarray, np.ndarray]:
    """Advance the state across a gap with no observations.

    The level integrates a decaying velocity rather than a constant one, so a short
    burst of improvement cannot compound into an unbounded forecast.
    """
    decay = float(np.exp(-_DECAY_RATE * dt_years))
    velocity = float(x[1])
    displacement = velocity * (1.0 - decay) / _DECAY_RATE
    x = np.array([float(x[0]) + displacement, velocity * decay])

    transition = _F(dt_years)
    covariance = transition @ covariance @ transition.T + _Q(dt_years, q)
    covariance[0, 0] = min(covariance[0, 0], P0[0])
    covariance[1, 1] = min(covariance[1, 1], P0[1])
    return x, covariance


def _Q(dt_years: float, q: float) -> np.ndarray:
    dt = max(dt_years, 0.0)
    return q * np.array([[dt**3 / 3.0, dt**2 / 2.0], [dt**2 / 2.0, dt]])


def _run_filter(entity_id: UUID, as_of: datetime) -> tuple[np.ndarray, np.ndarray, list[UUID]]:
    q, _ = _params()
    observations_for_entity = build_observations(entity_id, as_of)
    x = _X0.copy()
    covariance = _P0.copy()
    contributing: list[UUID] = []
    last_t: datetime | None = None
    for observation in observations_for_entity:
        if last_t is not None:
            dt_years = _dt_years(observation.observed_at, last_t)
            transition = _F(dt_years)
            x = transition @ x
            covariance = transition @ covariance @ transition.T + _Q(dt_years, q)
        measurement_noise = _noise_for_schema(observation)
        innovation = (_H @ covariance @ _H.T).item() + measurement_noise
        gain = (covariance @ _H.T) / innovation
        residual = observation.value - (_H @ x).item()
        x = x + gain.flatten() * residual
        covariance = (np.eye(2) - gain @ _H) @ covariance
        contributing.extend(observation.event_ids)
        last_t = observation.observed_at
    if last_t is not None:
        gap = _dt_years(as_of, last_t)
        if gap > 0:
            x, covariance = _propagate_through_silence(x, covariance, gap, q)
    return x, covariance, contributing


def founder(entity_id: UUID, as_of: datetime) -> FounderScore:
    if _active_model() == "beta_binomial":
        from memory import score_fallback

        return score_fallback.founder(entity_id, as_of)
    state, covariance, contributing = _run_filter(entity_id, as_of)
    return FounderScore(
        entity_id=entity_id,
        as_of=as_of,
        mu=float(np.clip(state[0], 0.0, 1.0)),
        band=float(np.sqrt(max(covariance[0, 0], 0.0))),
        trend=float(state[1]),
        contributing_event_ids=contributing,
        model="kalman",
    )


def forecast(entity_id: UUID, as_of: datetime, k_days: int) -> tuple[float, float]:
    q, _ = _params()
    state, covariance, _ = _run_filter(entity_id, as_of)
    dt_years = float(k_days) / _DAYS_PER_YEAR
    state, covariance = _propagate_through_silence(state, covariance, dt_years, q)
    return float(np.clip(state[0], 0.0, 1.0)), float(np.sqrt(max(covariance[0, 0], 0.0)))
