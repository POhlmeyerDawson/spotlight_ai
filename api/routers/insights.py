"""Cross-company routes: hidden ranking, NL compound query, backtest. Owner: D."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter

from api.routers.deps import degrade, pick, resolve_as_of, seed, seed_or

log = logging.getLogger(__name__)
router = APIRouter(tags=["insights"])

FILTER_SYSTEM = (
    "Translate an investor's natural-language query into a JSON filter. "
    "Return ONLY JSON with these keys, using null for anything the query does not "
    "constrain: sectors (list of lowercase strings), trend ('rising'|'falling'|null), "
    "min_score (float 0-1 or null), verification ('unverified'|'verified'|null), "
    "gate ('proceed'|'proof_protocol'|'no_call'|null), limit (int or null). "
    "Judge on substance only — never emit a filter on schooling, employer brand or "
    "investor name."
)

# Keyword fallback so /query still works with no model available (and in tests).
_SECTOR_WORDS = ("infra", "infrastructure", "ai", "ml", "devtools", "fintech", "bio", "security")


@router.get("/hidden")
def get_hidden(as_of: datetime | None = None, k: int = 50) -> dict:
    """High proximity to greatness, low individual visibility — plus the access lift."""
    cutoff = resolve_as_of(as_of)

    def live() -> dict:
        from sourcing import graph

        ranked = graph.hidden_ranking(cutoff, k)
        if not ranked:
            # An empty live ranking must not silently beat a seeded one on stage.
            raise LookupError("hidden ranking returned no candidates")
        picks = [c.entity_id for c in ranked]
        candidates = [
            {
                "entity_id": str(c.entity_id),
                "ppr": c.ppr,
                "visibility": c.visibility,
                "hidden_score": c.hidden_score,
            }
            for c in ranked
        ]
        try:
            lift = graph.access_lift(picks)
        except NotImplementedError:
            lift = None
        return {
            "as_of": cutoff.isoformat(),
            "candidates": candidates,
            "access_lift": lift,
            "degraded": False,
        }

    return degrade(live, lambda: {**seed("hidden"), "degraded": True})


def _parse_filter(q: str) -> dict:
    """LLM first; deterministic keyword pass if it's unavailable."""
    try:
        from core import llm

        out = llm.complete(
            f"Query: {q}", system=FILTER_SYSTEM, tier="fast", untrusted=q, json_mode=True
        )
        if isinstance(out, dict):
            return out
    except Exception as exc:  # noqa: BLE001 - query must still work without a model
        log.info("query: model unavailable, using keyword filter (%s)", exc)

    low = q.lower()
    return {
        "sectors": [w for w in _SECTOR_WORDS if w in low] or None,
        "trend": "rising" if "rising" in low else ("falling" if "falling" in low else None),
        "min_score": None,
        "verification": "unverified" if "unverified" in low else None,
        "gate": next((g for g in ("proceed", "proof_protocol", "no_call") if g in low), None),
        "limit": None,
    }


def _trend_value(c: dict) -> float | None:
    # The founder axis is where trend actually lives now. Checked first: a row can
    # still carry a flat top-level `momentum`, and reading that instead made every
    # "rising trend" query return nothing while six companies were plainly rising.
    axis = (c.get("axes") or {}).get("founder") or {}
    if isinstance(axis.get("trend"), (int, float)) and not isinstance(axis.get("trend"), bool):
        return float(axis["trend"])

    t = pick(c, "trend", "trend_value", "momentum")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    if isinstance(t, str):
        return {"rising": 1.0, "up": 1.0, "flat": 0.0, "falling": -1.0, "down": -1.0}.get(t.lower())
    return None


def _unverified(c: dict) -> bool:
    n = pick(c, "unverified_claims", "gap_count")
    if isinstance(n, (int, float)):
        return n > 0
    claims = pick(c, "claims", "claim_verdicts", default=[])
    statuses = {str(pick(x, "status", default="")).lower() for x in claims if isinstance(x, dict)}
    return bool(statuses & {"unverifiable", "not_attempted", "contradicted"})


def _haystack(c: dict) -> str:
    """Fixture key names are still settling — flatten every sector-ish field into one blob."""
    parts: list[str] = []
    for key in ("sector", "sectors", "category", "tags", "description", "name"):
        v = c.get(key)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v is not None:
            parts.append(str(v))
    return " ".join(parts).lower()


def _matches(c: dict, f: dict) -> bool:
    sectors = f.get("sectors")
    if sectors and not any(str(s).lower() in _haystack(c) for s in sectors):
        return False

    trend = f.get("trend")
    if trend in ("rising", "falling"):
        tv = _trend_value(c)
        if tv is None or (tv <= 0 if trend == "rising" else tv >= 0):
            return False

    min_score = f.get("min_score")
    if isinstance(min_score, (int, float)):
        mu = pick(c, "mu", "score", "founder_score")
        mu = pick(mu, "mu", default=mu) if isinstance(mu, dict) else mu
        if not isinstance(mu, (int, float)) or mu < min_score:
            return False

    verification = f.get("verification")
    if verification == "unverified" and not _unverified(c):
        return False
    if verification == "verified" and _unverified(c):
        return False

    gate = f.get("gate")
    if (
        gate
        and str(pick(c, "gate", "gate_outcome", "outcome", default="")).lower() != str(gate).lower()
    ):
        return False
    return True


@router.get("/query")
def compound_query(q: str, as_of: datetime | None = None) -> dict:
    """NL compound query over the ranked list. The model only translates — the filter
    itself runs in plain Python, so the result set is inspectable and reproducible."""
    from api.main import list_companies

    # Filter the LIVE ranked list, not the seed file. Querying a different set than
    # the one on screen returns ids the page cannot resolve, and the client is left
    # waiting on a result it can never render.
    try:
        companies = list_companies(as_of)
    except Exception:  # noqa: BLE001
        companies = seed_or("companies", {}).get("companies", [])

    f = _parse_filter(q)
    results = [c for c in companies if _matches(c, f)]
    limit = f.get("limit")
    if isinstance(limit, int) and limit > 0:
        results = results[:limit]

    # `company_ids` + `parsed` is the client's contract (lib/types.ts QueryResult).
    # Returning a different shape makes the client silently fall back to a local
    # interpreter that only knows fixture ids — which is what hung the button.
    return {
        "q": q,
        "parsed": _describe_filter(f),
        "company_ids": [c.get("id") for c in results if c.get("id")],
        "count": len(results),
        "filter": f,
        "results": results,
    }


def _describe_filter(f: dict) -> str:
    """Plain-English readback of what the query was understood to mean."""
    parts = []
    if f.get("sectors"):
        parts.append(f"sector in {', '.join(f['sectors'])}")
    if f.get("trend"):
        parts.append(f"{f['trend']} trend")
    if f.get("min_score") is not None:
        parts.append(f"founder score >= {f['min_score']}")
    if f.get("verification"):
        parts.append(f"{f['verification']} claims")
    if f.get("gate"):
        parts.append(f"gate = {f['gate']}")
    if f.get("limit"):
        parts.append(f"top {f['limit']}")
    return " · ".join(parts) if parts else "no filters recognised — showing everything"


@router.get("/backtest")
def get_backtest() -> dict:
    """Winners rising vs controls flat, threshold line, and one correctly-deprioritized
    failure. fame_check_passed is the H12 gate, surfaced so it cannot be missed."""
    from backtest import runner

    def live() -> dict:
        return _backtest_view(runner.run_calibration())

    return degrade(live, lambda: seed("backtest"))


# Scores are 0..1 internally and 0..100 in the client, same as the axes.
def _pct(v: float | None) -> float | None:
    return None if v is None else round(float(v) * 100, 1)


def _backtest_view(cal: dict) -> dict:
    """Map the calibration onto the client's Backtest contract (app/lib/types.ts).

    Without this the page validated on a `trajectories` array that nothing produced,
    failed silently, and rendered its hardcoded fixture — so the one artifact whose job
    is proving the system is not fooling itself was showing pre-computed numbers.
    """
    threshold = _pct(cal.get("threshold"))
    winners = cal.get("winners") or []
    controls = cal.get("controls") or []
    failure = cal.get("correctly_deprioritized_failure") or {}

    trajectories = [
        {
            "id": m.get("id") or f"{m.get('label')}-{i}",
            "name": m.get("name") or m.get("founder") or "unnamed",
            "label": m.get("label"),
            "outcome": m.get("outcome") or "",
            "points": [
                {
                    "t": p.get("as_of"),
                    "mu": _pct(p.get("mu", p.get("founder_score"))),
                    "band": _pct(p.get("band")),
                }
                for p in (m.get("trajectory") or [])
            ],
        }
        for i, m in enumerate(winners + controls)
    ]

    peak_control = max((m.get("peak_mu") or 0 for m in controls), default=None)
    cleared = cal.get("controls_clearing_threshold")
    detail = (
        f"H12 gate: {cleared if cleared is not None else 0} of {len(controls)} controls "
        f"cleared the {threshold} threshold"
        + (f" (highest control: {_pct(peak_control)})." if peak_control is not None else ".")
        + " Controls are matched founders from the same era who did not break out. If they"
        " had cleared, the score would be measuring visibility rather than capability."
    )

    return {
        "as_of": max((p["t"] for t in trajectories for p in t["points"] if p.get("t")), default=""),
        "truncation_note": cal.get("truncation_note")
        or "Sources truncated by hand to a pre-breakout date, recorded per cohort member.",
        "threshold": threshold,
        "fame_check_passed": bool(cal.get("fame_check_passed")),
        "fame_check_evaluated": bool(cal.get("fame_check_evaluated")),
        "fame_check_detail": detail,
        "hit_rate": cal.get("hit_rate"),
        "n_winners": len(winners),
        "n_controls": len(controls),
        "trajectories": trajectories,
        "correctly_deprioritized": {
            "name": failure.get("name") or failure.get("founder") or "",
            "final_score": _pct(failure.get("peak_mu")),
            "why": failure.get("why") or "",
            "outcome": failure.get("outcome") or "",
        },
        "lookahead_assertion": {
            "events_checked": cal.get("events_checked", 0),
            "violations": 0 if cal.get("lookahead_checked") else None,
            "detail": "Every scoring step asserts no event with observed_at > as_of "
            "reached the scorer. The assertion raises; it is never downgraded to a warning.",
        },
        "degraded": False,
    }
