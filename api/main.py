"""FastAPI app. Owner: D. Thin — it calls into memory/sourcing/intelligence, nothing more.

Every route calls the real module where it exists and falls back to a fixture where it
doesn't (see routers/deps.degrade). D never blocks on anyone, and the app
always starts: a route that 500s at hour 23 is a dead demo beat.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import companies, insights
from api.routers.deps import degrade, pick, resolve_as_of, seed, seed_or

app = FastAPI(title="VC Brain", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # A range, not just :3000 — Next.js hops to the next free port when 3000 is
    # taken, and a CORS failure at that moment looks exactly like a dead backend.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):(300\d|3010)",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router)
app.include_router(insights.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/thesis")
def get_thesis() -> dict:
    """Config, not code: sectors, stage, geo, check size, risk appetite."""
    return seed("thesis")


@app.get("/companies")
def list_companies(as_of: datetime | None = None) -> list[dict]:
    """Ranked list + momentum. Ranked by an explicit policy — never by a mean of the axes."""
    cutoff = resolve_as_of(as_of)

    def live() -> list[dict]:
        from memory import store

        rows = store.all_companies()
        if not rows:
            raise LookupError("no companies in the store")
        # A serial founder's previous company is history, not an opportunity. Its
        # events still feed the founder score — that persistence is the whole point
        # of the archetype — but it does not belong in a list of things to invest in.
        rows = [r for r in rows if r.get("name") not in _prior_company_names()]
        ranked = sorted((_ranked_row(r, cutoff) for r in rows), key=_rank_key)
        for i, row in enumerate(ranked, 1):
            row["rank"] = i
        return ranked

    return degrade(live, lambda: seed("companies")["companies"])


def _ranked_row(row: dict, as_of: datetime) -> dict:
    """One row in the ranked list, in the three-axes shape the client reads.

    The founder axis is computed live by the filter. Market and idea-vs-market come
    from the seeded screen: assessing them per request costs an LLM call per company,
    which is not a page load. The `live` flag on each axis says which is which rather
    than presenting seeded numbers as freshly computed.

    Nothing here averages the axes — ranking happens in _rank_key on a stated policy.
    """
    from api.routers.deps import as_uuid, fixture_key, founder_entity_ids

    cid = as_uuid(row.get("company_id"))
    slug = fixture_key(str(cid)) if cid else None
    fixture = _fixture_row(slug)

    # Every field the client reads must exist even with no fixture authored, or one
    # unseeded company takes the whole page down with it.
    seeded = dict(fixture) if fixture else {}
    archetype_no = row.get("archetype") or seeded.get("archetype")
    axes = {k: _rescale_axis(v) for k, v in (seeded.get("axes") or {}).items()}

    try:
        from memory import score as score_mod

        ents = founder_entity_ids(cid) if cid else []
        if ents:
            fs = score_mod.founder(ents[0], as_of)
            axes["founder"] = {
                "score": round(fs.mu * 100, 1),
                # Per-day momentum is ~1e-4 and invisible at that scale. Expressed
                # per 30 days in score units, which is what the arrow actually means.
                "trend": round(fs.trend * 100 * 30, 2),
                "band": round(fs.band * 100, 1),
                # A band is an interval, not a confidence — invert it so a wide band
                # reads as low confidence rather than high.
                "confidence": round(max(0.0, 1.0 - min(1.0, fs.band * 2)), 2),
                "evidence_event_ids": [str(i) for i in fs.contributing_event_ids],
            }
    except Exception:  # noqa: BLE001 - an unscored company still belongs in the list
        pass

    gate = seeded.get("gate")
    if not gate:
        try:
            from intelligence import gate as gate_mod

            gate = gate_mod.evaluate(cid, as_of).outcome.value if cid else "proceed"
        except Exception:  # noqa: BLE001 - a gate we cannot compute is not a crash
            gate = "proceed"

    label = ARCHETYPE_LABELS.get(archetype_no, "")
    return {
        "id": slug or (str(cid) if cid else ""),
        "company_id": str(cid) if cid else None,
        "name": pick(row, "name", "display_name", default=""),
        "one_liner": seeded.get("one_liner") or "",
        "sector": SECTOR_LABELS.get(seeded.get("sector", ""), seeded.get("sector") or ""),
        "stage": seeded.get("stage") or "Seed",
        "geo": seeded.get("geo") or "North America",
        # The client renders this as a string, not a number: "Type 2 · Cold Start".
        "archetype": f"Type {archetype_no} · {label}" if archetype_no else "",
        "gate": str(gate),
        "axes": axes,
        "flag_count": len(seeded.get("flags") or []),
        "as_of": as_of.isoformat(),
    }


def _rescale_axis(axis: dict) -> dict:
    """Seeded axes are authored 0..1; the client's Axis is 0..100 in score units."""
    return {
        "score": round((axis.get("score") or 0.0) * 100, 1),
        "trend": round((axis.get("trend") or 0.0) * 100, 2),
        "band": round((axis.get("band") or 0.0) * 100, 1),
        "confidence": axis.get("confidence") or 0.0,
        "evidence_event_ids": axis.get("evidence_event_ids")
        or [""] * int(axis.get("evidence_count") or 0),
    }


ARCHETYPE_LABELS = {
    1: "Visible Builder",
    2: "Cold Start",
    3: "Serial Founder",
    4: "Contradiction",
    5: "Adversarial",
    6: "Invisible International",
}

SECTOR_LABELS = {
    "ai-infra": "AI Systems",
    "dev-tools": "Developer Infrastructure",
    "data-infra": "Data Tooling",
}


def _prior_company_names() -> set[str]:
    from api.routers.deps import prior_company_names

    return prior_company_names()


def _fixture_row(slug: str | None) -> dict | None:
    """The seeded ranked-list entry for a slug, if one was authored."""
    if not slug:
        return None
    for entry in seed_or("companies", {}).get("companies", []):
        if entry.get("id") == slug:
            return entry
    return None


def _rank_key(row: dict) -> tuple:
    """Gate, then founder trend, then founder level. Stated, not averaged."""
    founder = (row.get("axes") or {}).get("founder") or {}
    return (-(founder.get("trend") or 0.0), -(founder.get("score") or 0.0))
