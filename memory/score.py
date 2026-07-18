"""Founder Score: local-linear-trend Kalman filter. Owner: A. See A.md H8-12.

    x_t = [mu, nu]        mu = capability level, nu = momentum
    F   = [[1, dt], [0, 1]]
    Score = mu   Band = sqrt(P[0,0])   Trend = nu (structural, never a diff of scores)

Contradicted claims must never become observations — filter at the boundary here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import numpy as np

from core import config
from memory import store
from schema.events import Event, EventKind, FounderScore, Source

log = logging.getLogger(__name__)

# --- calibration knobs (grid-searched against the 50 labels at H12-16) -------
Q_ACCEL = 2e-9  # process noise: white-noise-acceleration intensity, per day
R0 = 0.01  # base observation noise (variance) — std 0.10 on a green-flag read
MU0 = 0.5  # prior: we know nothing, and we say so with a wide band
NU0 = 0.0
P0 = (0.25, 1e-4)  # diag(var_mu, var_nu). band starts at 0.5, momentum prior +/-0.01/day.
DT_MIN = 1e-3  # co-timestamped events must not collapse F to a singular step

# Self-reported is noisier. deck > web > github/proof (SHARED §3 source list).
SOURCE_PENALTY: dict[str, float] = {
    Source.DECK: 2.0,
    Source.MANUAL: 1.2,
    Source.WEB: 1.0,
    Source.HN: 0.9,
    Source.ARXIV: 0.7,
    Source.GITHUB: 0.6,
    Source.VALIDATOR: 0.6,
    Source.PROOF_PROTOCOL: 0.15,
}
DEFAULT_SOURCE_PENALTY = 1.0

# Proof events are fresh, verified and behavioral — they move the score hard.
KIND_NOISE: dict[str, float] = {
    EventKind.GREEN_FLAG: 1.0,
    EventKind.PROOF_ARTIFACT: 0.2,
    EventKind.PROOF_BEHAVIOR: 0.2,
}

OBSERVATION_KINDS = tuple(KIND_NOISE)
_CLAIM_REF_KEYS = ("claim_id", "claim_ids", "supporting_claim_ids", "supporting_claims")

H = np.array([[1.0, 0.0]])


@dataclass(frozen=True)
class Observation:
    event_id: UUID
    observed_at: datetime
    y: float  # in [0, 1]
    r: float  # observation noise


@dataclass(frozen=True)
class ObservationSet:
    kept: list[Observation]
    dropped_contradicted: list[UUID]  # receipts for what the validator killed


# ---------------------------------------------------------------------------
# Boundary: events -> observations. C writes these payloads in parallel, so every
# shape here is best-effort — an unfamiliar payload is skipped, never fatal.
# ---------------------------------------------------------------------------


def _as_uuid(v: object) -> UUID | None:
    if isinstance(v, UUID):
        return v
    try:
        return UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        return None


def _claim_refs(payload: dict) -> set[UUID]:
    refs: set[UUID] = set()
    for key in _CLAIM_REF_KEYS:
        v = payload.get(key)
        items = v if isinstance(v, (list, tuple)) else [v]
        for item in items:
            if isinstance(item, dict):
                item = item.get("claim_id")
            if (u := _as_uuid(item)) is not None:
                refs.add(u)
    return refs


def contradicted_claim_ids(as_of: datetime) -> set[UUID]:
    """Claim ids the validator has ruled CONTRADICTED as of `as_of`.

    Not entity-scoped: claim ids are globally unique UUIDs and a validator verdict
    is written against the company, so scoping by entity would silently miss them.
    """
    out: set[UUID] = set()
    for ev in store.events(as_of=as_of, kind=EventKind.VALIDATION_RESULT):
        for entry in _verdict_entries(ev.payload):
            status = str(entry.get("status") or entry.get("verdict") or "").lower()
            if status.endswith("contradicted"):  # tolerates "ClaimStatus.CONTRADICTED"
                if (u := _as_uuid(entry.get("claim_id"))) is not None:
                    out.add(u)
    return out


def _verdict_entries(payload: dict) -> list[dict]:
    nested = payload.get("claims") or payload.get("verdicts")
    if isinstance(nested, list):
        return [e for e in nested if isinstance(e, dict)]
    return [payload]


def _derive_y(payload: dict) -> float | None:
    """Scalar reading, or a weighted YES-rate over a flag list. None = unknown shape."""
    for key in ("value", "y", "yes_rate", "score"):
        v = payload.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(np.clip(v, 0.0, 1.0))

    flags = payload.get("flags")
    if isinstance(flags, list) and flags:
        num = den = 0.0
        for f in flags:
            if not isinstance(f, dict):
                continue
            w = f.get("weight", 1.0)
            w = float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) else 1.0
            den += w
            if bool(f.get("fired")):
                num += w
        if den > 0:
            return float(np.clip(num / den, 0.0, 1.0))
    return None


def _noise(ev: Event, payload: dict) -> float:
    sc = payload.get("self_consistency")
    if not isinstance(sc, (int, float)) or isinstance(sc, bool):
        sc = ev.confidence
    sc = float(np.clip(sc, 0.1, 1.0))  # never let a zero divide blow the filter up
    penalty = SOURCE_PENALTY.get(str(ev.source), DEFAULT_SOURCE_PENALTY)
    return R0 / sc * penalty * KIND_NOISE.get(str(ev.kind), 1.0)


def observations(entity_id: UUID, as_of: datetime) -> ObservationSet:
    contradicted = contradicted_claim_ids(as_of)
    kept: list[Observation] = []
    dropped: list[UUID] = []

    for ev in store.events(as_of=as_of, entity_id=entity_id):
        if str(ev.kind) not in KIND_NOISE:
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        if _claim_refs(payload) & contradicted:
            dropped.append(ev.event_id)
            continue
        y = _derive_y(payload)
        if y is None:
            log.debug("score: unrecognised payload shape on %s (%s), skipped", ev.event_id, ev.kind)
            continue
        kept.append(Observation(ev.event_id, ev.observed_at, y, _noise(ev, payload)))

    if dropped:
        log.info("score: dropped %d contradicted observation(s): %s", len(dropped), dropped)
    return ObservationSet(kept, dropped)


# ---------------------------------------------------------------------------
# The filter
# ---------------------------------------------------------------------------


def _transition(dt: float) -> tuple[np.ndarray, np.ndarray]:
    """F and Q for a dt-day step. Q is the continuous white-noise-acceleration
    integral, so process noise scales with elapsed time rather than event count —
    a founder silent for a year should widen, one shipping weekly should not."""
    f = np.array([[1.0, dt], [0.0, 1.0]])
    q = Q_ACCEL * np.array([[dt**3 / 3.0, dt**2 / 2.0], [dt**2 / 2.0, dt]])
    return f, q


def _run(obs: list[Observation], until: datetime) -> tuple[np.ndarray, np.ndarray]:
    """Filter the observations, then predict forward to `until`.

    That final predict is what makes silence cost something: a founder whose last
    signal was a year ago must not keep the tight band they earned back then.
    """
    x = np.array([MU0, NU0])
    p = np.diag(P0).astype(float)
    prev: datetime | None = None

    for o in obs:
        dt = max((o.observed_at - prev).total_seconds() / 86400.0, DT_MIN) if prev else 0.0
        if dt:
            x, p = _predict(x, p, dt)
        s = (H @ p @ H.T).item() + o.r
        k = (p @ H.T / s).reshape(2)
        x = x + k * (o.y - (H @ x).item())
        p = (np.eye(2) - np.outer(k, H)) @ p
        prev = o.observed_at

    if prev is not None:
        gap = max((until - prev).total_seconds() / 86400.0, 0.0)
        if gap:
            x, p = _predict(x, p, gap)
    return x, p


def _predict(x: np.ndarray, p: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
    f, q = _transition(dt)
    return f @ x, f @ p @ f.T + q


def founder(entity_id: UUID, as_of: datetime) -> FounderScore:
    if config.settings.score_model == "beta_binomial":
        from memory import score_fallback  # lazy: fallback imports observations() from here

        return score_fallback.founder(entity_id, as_of)

    obs = observations(entity_id, as_of)
    x, p = _run(obs.kept, as_of)
    return FounderScore(
        entity_id=entity_id,
        as_of=as_of,
        # Clamped: momentum extrapolated across a long silence can leave [0,1], and a
        # capability level of 1.3 is not a score, it's a display bug. The band stays honest.
        mu=float(np.clip(x[0], 0.0, 1.0)),
        band=float(np.sqrt(p[0, 0])),
        trend=float(x[1]),
        contributing_event_ids=[o.event_id for o in obs.kept],
    )


def forecast(entity_id: UUID, as_of: datetime, k_days: int) -> tuple[float, float]:
    """k-step prediction interval — propagate P forward. Falls out of the filter:
    the observations are still as_of-scoped, only the predict horizon moves."""
    obs = observations(entity_id, as_of).kept
    x, p = _run(obs, as_of + timedelta(days=k_days))
    return float(np.clip(x[0], 0.0, 1.0)), float(np.sqrt(p[0, 0]))
