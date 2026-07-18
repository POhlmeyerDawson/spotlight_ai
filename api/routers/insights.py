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
        return {"as_of": cutoff.isoformat(), "candidates": candidates, "access_lift": lift,
                "degraded": False}

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
        return {"rising": 1.0, "up": 1.0, "flat": 0.0, "falling": -1.0, "down": -1.0}.get(
            t.lower()
        )
    return None


def _unverified(c: dict) -> bool:
    n = pick(c, "unverified_claims", "gap_count")
    if isinstance(n, (int, float)):
        return n > 0
    claims = pick(c, "claims", "claim_verdicts", default=[])
    statuses = {
        str(pick(x, "status", default="")).lower() for x in claims if isinstance(x, dict)
    }
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
    if gate and str(pick(c, "gate", "gate_outcome", "outcome", default="")).lower() != str(
        gate
    ).lower():
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

    return degrade(runner.run_calibration, lambda: seed("backtest"))
