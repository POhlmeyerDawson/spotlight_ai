"""The thesis engine — S0. Config, not code.

The spec's first stage is "config not code: sectors, stage, geo, check size, risk
appetite". `data/seed/thesis.json` was rich and complete, and NOTHING READ IT. The
API served it over GET, the dashboard rendered it and let you edit it, and every
downstream decision ignored every field. `ranking_policy` was even duplicated as a
literal in the API rather than read from here. Editing the thesis changed nothing,
which makes it a picture of a control panel.

This module is the reader. It is deliberately the ONLY place that interprets the
file, so "what does the thesis actually change?" has one answer.

What it governs, and what it explicitly does not:

  SECTORS        in-scope sectors, with weights. Out-of-scope companies are
                 EXCLUDED FROM THE PIPELINE, not scored down — a fund that does not
                 invest in a sector does not rank it lower, it does not look at it.
  STAGE          same treatment.
  GEO            deliberately unrestricted by default, and the config says why:
                 "geographic filters are the cheapest way to systematically miss
                 the Type 6 founder". Honoured, but a filter set here is applied.
  CHECK_SIZE     min/target/max, read by the recommendation.
  RISK_APPETITE  0..1, where 1 is pre-product technical conviction. Moves the
                 EVIDENCE BAR, not the score: a high-risk-appetite fund proceeds on
                 thinner evidence. It must never move the score itself, or the same
                 founder would be more capable at a bolder fund, which is nonsense.

Never touches: the founder score, the green-flag rules, or anything a founder is
measured by. The thesis says what WE are looking for. It does not get to change
what is true about them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

def seed_path() -> Path:
    """Where the thesis lives.

    Honours VCBRAIN_SEED_DIR, the same override api/routers/deps.py uses. Hardcoding
    the path made this module read the production config even under test isolation —
    so a test pointing at a tmp fixture silently asserted against the real file, and
    any deployment with a relocated seed dir would have been ignored without a word.
    """
    import os

    return Path(os.getenv("VCBRAIN_SEED_DIR", "data/seed")) / "thesis.json"

# Applied when the file is missing or a field is absent. Permissive by design: a
# missing thesis must not silently filter the pipeline to nothing.
DEFAULTS: dict[str, Any] = {
    "sectors": [],
    "stage": {"include": [], "exclude": []},
    "geo": {"include": ["global"], "excluded_regions": []},
    "check_size": {"currency": "USD", "min": 250_000, "target": 750_000, "max": 2_000_000},
    "risk_appetite": {"value": 0.5},
    "ranking_policy": {"id": "min_axis_with_momentum_tiebreak"},
}


#: Key under which the edited thesis is stored in core.state's config_documents.
DOCUMENT_KEY = "thesis"


def load(path: Path | None = None) -> dict:
    """The live thesis: the stored edit if there is one, else the file, else DEFAULTS.

    THE STORED DOCUMENT WINS, and it has to. `data/seed/thesis.json` is the only mutable
    config this system kept on disk, and the deployment filesystem is read-only — so on
    Vercel an edit can only ever land in Postgres. If this reader kept looking only at
    the file, editing the thesis would change what the panel displays and nothing else,
    which is precisely the "picture of a control panel" this module was written to end.

    An explicit `path` still forces the file, because a caller naming a file means a file
    — that is what keeps tests pointing at a tmp fixture honest.
    """
    if path is None:
        from core import state

        stored = state.get_document(DOCUMENT_KEY)
        if stored is not None:
            return {**DEFAULTS, **stored}

    p = path or seed_path()
    if not p.exists():
        return dict(DEFAULTS)
    blob = json.loads(p.read_text())
    return {**DEFAULTS, **blob}


def _norm(v: Any) -> str:
    return str(v or "").strip().lower().replace("_", "-").replace(" ", "-")


def included_sectors(thesis: dict | None = None) -> set[str]:
    t = thesis or load()
    out = set()
    for s in t.get("sectors") or []:
        if isinstance(s, dict) and s.get("include", True):
            out |= {_norm(s.get("id")), _norm(s.get("label"))}
        elif isinstance(s, str):
            out.add(_norm(s))
    return {x for x in out if x}


def sector_weight(sector: str, thesis: dict | None = None) -> float:
    """Weight for a sector, 1.0 when unspecified. Used for ordering emphasis only —
    never folded into an axis, because the axes are never blended."""
    t = thesis or load()
    n = _norm(sector)
    for s in t.get("sectors") or []:
        if isinstance(s, dict) and n in {_norm(s.get("id")), _norm(s.get("label"))}:
            w = s.get("weight")
            return float(w) if isinstance(w, (int, float)) else 1.0
    return 1.0


def in_scope(
    *, sector: str | None = None, stage: str | None = None, geo: str | None = None,
    thesis: dict | None = None,
) -> tuple[bool, str | None]:
    """Is this company something we look at at all?

    Returns (in_scope, reason_if_not). An UNKNOWN value is IN scope: absent data is
    not disqualifying, which is the same rule the gate's absence classifier applies
    and the reason a Type 6 founder with sparse metadata is not quietly dropped.
    """
    t = thesis or load()

    allowed = included_sectors(t)
    if sector and allowed and _norm(sector) not in allowed:
        return False, f"sector {sector!r} is outside the thesis"

    st = t.get("stage") or {}
    if stage:
        if _norm(stage) in {_norm(x) for x in st.get("exclude") or []}:
            return False, f"stage {stage!r} is excluded by the thesis"
        include = {_norm(x) for x in st.get("include") or []}
        if include and _norm(stage) not in include:
            return False, f"stage {stage!r} is outside the thesis"

    g = t.get("geo") or {}
    if geo and _norm(geo) in {_norm(x) for x in g.get("excluded_regions") or []}:
        return False, f"geography {geo!r} is excluded by the thesis"

    return True, None


def risk_appetite(thesis: dict | None = None) -> float:
    t = thesis or load()
    r = t.get("risk_appetite")
    if isinstance(r, dict):
        v = r.get("value")
        return float(v) if isinstance(v, (int, float)) else 0.5
    return float(r) if isinstance(r, (int, float)) else 0.5


def evidence_bar(thesis: dict | None = None) -> float:
    """How much certainty the gate should demand before proceeding, as a band ceiling.

    This is the one knob that genuinely changes a decision, so it is worth being
    precise about the direction: HIGHER risk appetite means a WIDER acceptable band
    — a fund with pre-product conviction proceeds on thinner evidence. It moves what
    we require, never what the founder scored.

    appetite 0.0 -> 0.10 (only well-evidenced companies clear)
    appetite 0.5 -> 0.20
    appetite 1.0 -> 0.30 (pre-product technical conviction)
    """
    return round(0.10 + 0.20 * max(0.0, min(1.0, risk_appetite(thesis))), 3)


def check_size(thesis: dict | None = None) -> dict:
    """Normalised {currency, min, target, max}.

    Tolerates a bare number, which some fixtures use — read as the target, with the
    range derived around it. A config reader that raises on a shape it did not
    anticipate takes the whole route down; the field is a fund parameter, not a
    schema the world is obliged to honour.
    """
    t = thesis or load()
    cs = t.get("check_size")
    if isinstance(cs, (int, float)) and not isinstance(cs, bool):
        target = float(cs)
        return {**DEFAULTS["check_size"], "min": target / 3, "target": target, "max": target * 2.5}
    if not isinstance(cs, dict):
        return dict(DEFAULTS["check_size"])
    return {**DEFAULTS["check_size"], **cs}


def ranking_policy_id(thesis: dict | None = None) -> str:
    t = thesis or load()
    rp = t.get("ranking_policy")
    if isinstance(rp, dict):
        return str(rp.get("id") or DEFAULTS["ranking_policy"]["id"])
    return str(rp or DEFAULTS["ranking_policy"]["id"])
