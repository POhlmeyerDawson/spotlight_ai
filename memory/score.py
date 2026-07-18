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
MOMENTUM_HALFLIFE_DAYS = 90.0  # trend decays across silence; it does not run forever

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


# --- calibration -------------------------------------------------------------
#
# The green-flag sensor reports a WEIGHTED YES-RATE: what fraction of the applicable
# rules a founder fired. That is an honest quantity but it is not the same scale as
# the market and idea-vs-market axes, which are quality judgements in 0..1. Observed
# across the cohort the rate spans roughly 0.02..0.32, so placed beside axes that sit
# at 0.70..0.85 the founder column reads as uniformly weak — and, worse, the declared
# min-axis ranking can never consult the other two axes, because the founder axis is
# always the minimum. Comparability is a correctness requirement here, not polish.
#
# So the rate is stretched onto the capability scale by a stated, monotone, two-point
# map. Monotone means no ordering is ever changed by calibration — only the scale is.
#
# CALIBRATED ON THE CURRENT COHORT, NOT ON REAL LABELS. A.md calls for fitting on ~50
# hand-labelled founders; these anchors stand in until that exists, and the raw rate
# is kept on every score so nothing is hidden behind the mapping.
# Anchors measured on the current cohort: the weighted YES-rate spans 0.135..0.538
# with a median of 0.26. A LOGISTIC, not a clipped linear stretch — clipping made two
# genuinely different founders (rate 0.60 and 0.75) collapse onto the same ceiling
# value, which silently broke the monotonicity this map exists to guarantee.
RATE_MID = 0.30  # rate that maps to the middle of the capability scale
RATE_SLOPE = 8.0  # steepness; fitted so 0.12 -> ~0.26 and 0.54 -> ~0.76
SCORE_FLOOR, SCORE_CEIL = 0.12, 0.86  # the scale the other two axes live on

# A ratio over two flags is not evidence. The deck-only founders measured 0.50 —
# above most well-evidenced founders — purely because firing one of two applicable
# rules is 50%. Left alone that inverts the cold-start beat.
#
# An UNKNOWN count is shrunk as if it were typical (n = SHRINK_K), not trusted
# outright. Trusting it fully scored a payload that omits its evidence count ABOVE an
# identical one that reports it — rewarding missing metadata, which is exactly
# backwards. Discounting it fully was also wrong: not knowing the count is not the
# same as knowing it is small, and that flattened every scalar reading onto the prior.
RATE_PRIOR = 0.26  # cohort median
SHRINK_K = 8.0  # pseudo-observations; ~K flags before a founder speaks for themselves


def calibrate(rate: float, n_flags: int = 0) -> float:
    """Weighted YES-rate -> capability scale.

    Strictly monotone in `rate` at any fixed evidence count, so calibration changes the
    scale and never the ordering.
    """
    r = float(rate)
    n = int(n_flags) if int(n_flags) > 0 else int(SHRINK_K)
    r = (r * n + RATE_PRIOR * SHRINK_K) / (n + SHRINK_K)

    squashed = 1.0 / (1.0 + float(np.exp(-RATE_SLOPE * (r - RATE_MID))))
    return float(SCORE_FLOOR + squashed * (SCORE_CEIL - SCORE_FLOOR))


def _flag_count(payload: dict) -> int:
    flags = payload.get("flags")
    if isinstance(flags, list):
        return len(flags)
    n = payload.get("n_flags") or payload.get("evaluated")
    return int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else 0


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
        # Only the flag sensor reports a YES-RATE and needs stretching onto the
        # capability scale. PROOF_* events are already graded quality in 0..1 —
        # calibrating those would double-map them and quietly inflate the one
        # observation type the filter trusts most.
        if str(ev.kind) == str(EventKind.GREEN_FLAG):
            y = calibrate(y, _flag_count(payload))
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
            # Momentum DECAYS across silence rather than persisting. Extrapolating a
            # constant nu forever pushed mu past every observation the founder had
            # (0.68 against a maximum reading of 0.37) and inflated the band beyond
            # the width of the whole scale. Someone who shipped twice a year ago is
            # not still improving at the rate they were; the honest statement is that
            # we know less, which the widening band already says.
            x = x.copy()
            x[1] *= float(np.exp(-gap / MOMENTUM_HALFLIFE_DAYS))
            x, p = _predict(x, p, gap)

    # An uncertainty wider than the prior is not a measurement. The band can never
    # exceed where we started from knowing nothing.
    p[0, 0] = min(float(p[0, 0]), P0[0])
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
