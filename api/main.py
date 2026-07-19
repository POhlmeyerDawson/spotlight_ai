"""FastAPI app. Owner: D. Thin — it calls into memory/sourcing/intelligence, nothing more.

Every route calls the real module where it exists and falls back to a fixture where it
doesn't (see routers/deps.degrade). D never blocks on anyone, and the app
always starts: a route that 500s at hour 23 is a dead demo beat.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.routers import applications, auth, companies, insights, outbound, personal, profile
from api.routers.deps import degrade, pick, resolve_as_of, seed, seed_or

log = logging.getLogger(__name__)

app = FastAPI(title="VC Brain", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # A range, not just :3000 — Next.js hops to the next free port when 3000 is
    # taken, and a CORS failure at that moment looks exactly like a dead backend.
    # Local dev ports, plus any *.vercel.app origin. In production the frontend and
    # the API are served from ONE deployment, so calls are same-origin and never
    # reach this check — the vercel pattern is here for preview deployments and for
    # the case where the API is split onto its own host.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):(300\d|3010)|https://[a-z0-9-]+\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
    # Required for the session cookie to be sent from the Next.js dev origin. In
    # production both halves are same-origin so this never applies, which is exactly
    # why its absence would only have shown up in local development — and only after
    # login had been built. Safe here because the origins are an explicit regex, never
    # a wildcard: allow_credentials with "*" is what the spec forbids.
    allow_credentials=True,
)

app.include_router(companies.router)
app.include_router(insights.router)
app.include_router(outbound.router)
app.include_router(applications.router)
# Personalisation only. Everything above this line stays reachable without a session —
# a broken login must degrade to the objective product, never to a blank page.
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(personal.router)


@app.get("/health")
def health() -> dict:
    """Liveness plus the things that degrade QUIETLY, so they can be seen before a
    demo rather than discovered during one."""
    from core.config import settings

    out: dict = {"ok": True, "llm_provider": settings.llm_provider}
    out["github_authenticated"] = bool(settings.github_token)
    try:
        import httpx

        r = httpx.get(
            "https://api.github.com/rate_limit",
            timeout=4.0,
            headers={"Authorization": f"Bearer {settings.github_token}"}
            if settings.github_token
            else {},
        )
        core = r.json()["resources"]["core"]
        out["github_rate"] = {"remaining": core["remaining"], "limit": core["limit"]}
        # Unauthenticated is 60/hour and this IP has exhausted it before. A scanner
        # that returns nothing looks identical to a founder with no footprint.
        if core["remaining"] == 0:
            out["warnings"] = [
                "GitHub rate limit exhausted — live scanning will return nothing, which "
                "is indistinguishable from a founder having no public footprint. Set "
                "GITHUB_TOKEN in .env to raise the limit from 60/hr to 5000/hr."
            ]
    except Exception:  # noqa: BLE001 - health must never be the thing that fails
        out["github_rate"] = None
    return out


@app.get("/thesis")
def get_thesis() -> dict:
    """Config, not code: sectors, stage, geo, check size, risk appetite.

    `applies_to` is included so the panel can state what editing this actually
    changes. The file was previously served, rendered, editable — and read by
    nothing, which made it a picture of a control panel.
    """
    from core import state
    from core import thesis as thesis_mod

    # The stored edit wins over the shipped file, matching core.thesis.load(). Without
    # this the panel would render the seed defaults straight after a successful save on
    # any deployment whose filesystem cannot be written — an edit that "worked" and then
    # visibly un-did itself on reload.
    t = state.get_document(thesis_mod.DOCUMENT_KEY) or seed("thesis")
    return {
        **t,
        "applies_to": {
            "pipeline_membership": "sectors, stage and geo exclude companies from /companies",
            "evidence_bar": thesis_mod.evidence_bar(t),
            "check_size": thesis_mod.check_size(t),
            "ranking_policy": thesis_mod.ranking_policy_id(t),
            "never_affects": "founder score, green-flag rules, or anything a founder "
            "is measured by — the thesis says what we look for, not what is true of them",
        },
    }


@app.put("/thesis")
def put_thesis(update: dict = Body(...)) -> dict:
    """Persist an edited thesis. The dashboard POSTed to a route that did not exist,
    so every edit was silently discarded the moment the page reloaded."""
    import json as _json

    from api.routers.deps import seed_dir
    from core import state
    from core import thesis as _thesis

    # Read the CURRENT stored thesis, never a cached copy. A copy predating another
    # process's edit gets written straight back out, silently deleting whatever they
    # added — which is exactly what happened to `clearing_score` mid-session. The thesis
    # is shared mutable state; a write must be based on its current contents. Postgres is
    # read first for the same reason it is written first: it is where the current value
    # lives once anyone has edited it.
    path = seed_dir() / "thesis.json"
    current = state.get_document(_thesis.DOCUMENT_KEY)
    if current is None:
        current = _json.loads(path.read_text()) if path.exists() else {}

    # Unknown keys are PRESERVED, not dropped. This endpoint does not know every field
    # the config will grow, and a config writer that silently discards what it does not
    # recognise destroys other people's work by default.
    merged = {**current, **{k: v for k, v in (update or {}).items() if k != "applies_to"}}

    # POSTGRES IS THE STORE, the file is a best-effort convenience.
    #
    # This is the one write in this codebase that must NOT degrade quietly. A cache that
    # fails to persist costs a recomputation; a THESIS that fails to persist is a user's
    # edit being discarded while the UI renders it back as saved — and the next page load
    # silently reverts it. So the ordering is: store it durably, and if that is
    # impossible, say so with a 503 rather than return 200 over a lost edit.
    saved = state.put_document(_thesis.DOCUMENT_KEY, merged)

    # Also mirror to disk when the filesystem allows it, so a local checkout keeps a
    # readable, diffable thesis.json and a DB-less dev box still works. Guarded: on the
    # read-only serverless filesystem this always fails, and it must not be the thing
    # that fails the request when Postgres already accepted the write.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(merged, indent=2) + "\n")
        saved = True
    except OSError:
        if not saved:
            raise HTTPException(
                503,
                "the thesis could not be saved — neither the database nor the config "
                "file accepted the write, so your edit was NOT persisted. Nothing was "
                "changed; retry once storage is reachable.",
            ) from None

    if hasattr(_thesis.load, "cache_clear"):
        _thesis.load.cache_clear()
    return get_thesis()


@app.get("/timing/{company_id}")
def get_timing(company_id: str, as_of: datetime | None = None) -> dict:
    """Signal-to-decision time — the rubric's "signal-to-decision time instrumented".

    Runs the decision stages for real and times them, rather than reporting a stored
    number. Two clocks that must travel together: `compute_ms` is what we can shorten,
    `signal_age_days` is how long the evidence existed before we ruled on it. A fast
    compute over stale evidence is not a fast decision.
    """
    from api.routers.deps import company_uuid, founder_entity_ids
    from core.timing import Stages, measure
    from memory import store

    cutoff = resolve_as_of(as_of)
    cid = company_uuid(company_id)
    if cid is None:
        raise HTTPException(404, f"unknown company: {company_id}")

    stages = Stages()
    with stages.stage("read_events"):
        events = store.events(as_of=cutoff, company_id=cid)

    with stages.stage("score_founder"):
        from memory import score as score_mod

        for ent in founder_entity_ids(cid)[:1]:
            score_mod.founder(ent, cutoff)

    with stages.stage("gate"):
        try:
            from intelligence import gate as gate_mod

            gate_mod.evaluate(cid, cutoff)
        except Exception:  # noqa: BLE001 - a stage that fails still took time
            pass

    return measure(cid, cutoff, stages, events)


@app.get("/companies")
def list_companies(as_of: datetime | None = None) -> list[dict]:
    """Ranked list + momentum. Ranked by an explicit policy — never by a mean of the axes."""
    cutoff = resolve_as_of(as_of)

    def live() -> list[dict]:
        from memory import score as score_mod, store

        # Read-only request: the store cannot change under us, so the scorer's pure
        # reads are memoized for its duration. Without this every company runs the
        # Kalman filter twice (here and again inside intelligence.gate.evaluate) and
        # re-scans the entire validation-event corpus each time — the two terms that
        # made this endpoint superlinear in corpus size.
        with score_mod.scoring_cache():
            return _live_rows(cutoff)

    def _live_rows(cutoff: datetime) -> list[dict]:
        from memory import store

        rows = store.all_companies()
        if not rows:
            raise LookupError("no companies in the store")
        # A serial founder's previous company is history, not an opportunity. Its
        # events still feed the founder score — that persistence is the whole point
        # of the archetype — but it does not belong in a list of things to invest in.
        excluded = _prior_company_names() | _backtest_cohort_names()
        rows = [r for r in rows if r.get("name") not in excluded]
        # The slug is resolved ONCE per row and threaded through both the thesis filter
        # and the row build. Both used to derive it independently via
        # deps.fixture_key(), so every company paid two identical database round trips
        # before any of its actual work began.
        slugged = [(r, _slug_for_row(r)) for r in rows]
        slugged = [(r, s) for r, s in slugged if _in_thesis_scope(r, s)]
        # One query for the whole as_of-scoped log instead of one per company. The gate
        # and the founder score each read events for every row, so this path issued
        # ~2 * N round trips to a hosted Postgres; at 126 companies that was the 48-70s
        # that made the browser give up and render fixtures.
        with store.prefetch(cutoff):
            ranked = sorted((_ranked_row(r, cutoff, slug=s) for r, s in slugged), key=_rank_key)
        for i, row in enumerate(ranked, 1):
            row["rank"] = i
        return ranked

    return degrade(live, lambda: [_seeded_row(e, cutoff) for e in seed("companies")["companies"]])


def _seeded_row(entry: dict, as_of: datetime) -> dict:
    """A ranked-list row served from the fixture because the store was unreachable.

    The degraded list path used to return the authored fixture entries VERBATIM: no
    gate_source, no `live` flags, no axis normalization. So the one path where every
    number is definitionally hand-authored was also the only path that said nothing
    about it — a store outage silently turned the whole list into undisclosed fixtures.
    Extra keys are additive, and the raw fixture keys are preserved because /query
    filters on them.
    """
    axes = {k: _rescale_axis(v) for k, v in (entry.get("axes") or {}).items()}
    return {
        **entry,
        "axes": axes,
        "gate": entry.get("gate"),
        "gate_source": "seeded_fixture" if entry.get("gate") else "unavailable",
        "gate_rationale": None,
        "as_of": as_of.isoformat(),
        "degraded": True,
    }


def _ranked_row(
    row: dict, as_of: datetime, *, compute: bool = False, slug: str | None = None
) -> dict:
    """One row in the ranked list, in the three-axes shape the client reads.

    The founder axis is computed live by the filter every request. Market and
    idea-vs-market each cost an LLM call, so the list serves them only when a screening
    has already been computed for this company (see deps.screening); otherwise it says
    so. Every axis carries `live`: True means these numbers were computed from the event
    log, False means they were read from a hand-authored seed. An axis with no computed
    receipts carries an EMPTY evidence list and a `reason` — never a padded placeholder.

    Nothing here averages the axes — ranking happens in _rank_key on a stated policy.
    """
    from api.routers.deps import as_uuid, screening

    cid = as_uuid(row.get("company_id"))
    if slug is None:
        slug = _slug_for_row(row)
    fixture = _fixture_row(slug)

    # Every field the client reads must exist even with no fixture authored, or one
    # unseeded company takes the whole page down with it.
    seeded = dict(fixture) if fixture else {}
    archetype_no = row.get("archetype") or seeded.get("archetype")
    axes = {k: _rescale_axis(v) for k, v in (seeded.get("axes") or {}).items()}

    # A computed screening replaces the seeded market / idea-vs-market axes wholesale,
    # receipts included. This is the only path on which those two axes are ever `live`.
    screen_result = screening(cid, as_of, compute=compute) if cid else None
    if screen_result is not None:
        for name in ("market", "idea_vs_market"):
            axis = getattr(screen_result, name, None)
            if axis is not None:
                axes[name] = _axis_from_screen(axis)

    try:
        from memory import score as score_mod

        ents = _founder_ids_for_row(row) if cid else []
        if ents:
            fs = score_mod.founder(ents[0], as_of)
            axes["founder"] = {
                "score": round(fs.mu * 100, 1),
                # Expressed per 30 days in score units, which is what the arrow means.
                "trend": round(fs.trend * 100 * _TREND_YEARS_PER_30_DAYS, 2),
                "trend_unit": TREND_UNIT_SCORE_PER_30D,
                "band": round(fs.band * 100, 1),
                # A band is an interval, not a confidence — invert it so a wide band
                # reads as low confidence rather than high.
                "confidence": round(max(0.0, 1.0 - min(1.0, fs.band * 2)), 2),
                "evidence_event_ids": [str(i) for i in fs.contributing_event_ids],
                "live": True,
            }
    except Exception:  # noqa: BLE001 - an unscored company still belongs in the list
        pass

    gate, gate_source, gate_rationale = _gate_for(cid, as_of, seeded)

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
        # 'sourced' | 'constructed' — whether this company's evidence was collected
        # from the outside world or AUTHORED for this repo. Read from the store row,
        # never from `seeded`: the fixture is exactly the thing whose authorship is in
        # question here, so letting it describe its own provenance would be circular.
        # Defaulting to 'constructed' when the store row is silent is the safe
        # direction — an unlabelled row is one nobody has vouched for, and calling
        # that sourced is the specific mistake this field exists to prevent.
        "provenance": row.get("provenance") or "constructed",
        "gate": gate,
        # Which of the two produced `gate`. A fixture standing in for the engine is a
        # thing a judge is entitled to see, so it is stated rather than silently served.
        "gate_source": gate_source,
        "gate_rationale": gate_rationale,
        "axes": axes,
        "flag_count": len(seeded.get("flags") or []),
        "as_of": as_of.isoformat(),
        # Beyond the client's CompanySummary contract — TypeScript ignores extra
        # fields, and /query filters on these. Dropping them silently made every
        # "unverified"/sector query return nothing.
        "flags": seeded.get("flags") or [],
        "unverified_claims": seeded.get("unverified_claims") or 0,
        "sector_key": seeded.get("sector") or "",
        "archetype_no": archetype_no,
        # What stood out, IF it has already been computed. Never generated inline —
        # api/standout.py's comparison plus its one LLM call would be ~90s across
        # thirteen rows, the same arithmetic that keeps the market axis off this path.
        # An uncomputed row carries an explicit `status: not_generated` and a null
        # summary, so the card renders "not yet generated" instead of a blank line that
        # would read as a finding of nothing.
        "standout": _standout_for(cid, as_of),
    }


def _standout_for(cid, as_of: datetime) -> dict:
    """The cached standout summary for a row, or the explicit not-generated marker."""
    from api import standout

    if cid is None:
        return standout.not_generated("")
    try:
        hit = standout.cached(cid, as_of)
    except Exception as exc:  # noqa: BLE001 - a missing summary must not drop a row
        log.info("standout unavailable for %s (%s)", cid, exc)
        hit = None
    return hit or standout.not_generated(cid)


def _gate_for(cid, as_of: datetime, seeded: dict) -> tuple[str | None, str, str | None]:
    """The gate, and where it came from. THE ENGINE WINS.

    The engine is the thing that actually decides; a hand-authored `gate` in the seed
    is a placeholder for it. Serving the fixture in preference to the engine — which is
    what this did — made the API disagree with its own decision engine on 9 of 13
    companies while presenting the result as a decision. When the engine genuinely
    cannot answer we fall back, but `gate_source` names the fallback in the payload so
    an authored verdict can never again be read as a computed one.
    """
    if cid is not None:
        try:
            from intelligence import gate as gate_mod

            decision = gate_mod.evaluate(cid, as_of)
            return decision.outcome.value, "computed", decision.rationale
        except Exception:  # noqa: BLE001 - a gate we cannot compute is not a crash
            pass

    if seeded.get("gate"):
        return str(seeded["gate"]), "seeded_fixture", None
    # No engine answer and nothing authored. Previously this defaulted to "proceed" —
    # inventing the most permissive verdict in the system out of an absence of data.
    return None, "unavailable", "the decision engine could not be run for this company"


def _axis_from_screen(axis) -> dict:
    """A computed axis from intelligence.screen. Receipts are real event ids.

    Two things this deliberately does NOT do:

    `band` stays null. The client documents band as "± uncertainty in SCORE UNITS", and
    the screen does not produce one — deriving it from confidence would put a fabricated
    interval on the chart in the same units as the founder axis's real, filter-computed
    band, which is the authored-served-as-computed failure this whole change is about.
    Null renders as absence, which is the true answer.

    `trend` is NOT rescaled by 100. The screen's trend is an LLM-assigned DIRECTION in
    -1..1, not a rate; multiplying it by 100 produced a market trend of 100.0 on a 0..100
    axis, i.e. "this score moves 100 points in 30 days" — the same impossible-magnitude
    error as the founder trend's per-day/per-year mixup. The units genuinely differ per
    axis, so each axis states its own.
    """
    ids = [str(i) for i in (axis.evidence_event_ids or []) if str(i).strip()]
    # A null score is an axis we could not measure, not a zero. It stays null all the
    # way to the client, which renders absence rather than a number — multiplying it
    # by 100 would raise, and coalescing it to 0.0 would put "we did not look" on the
    # chart as the worst possible score.
    scored = axis.score is not None
    # getattr: `Axis` always has `reason`, but this serializer is also handed lightweight
    # stand-ins by callers and tests. A missing attribute is "no stated reason", not a crash.
    reason = getattr(axis, "reason", None) or (
        None if ids else "the screen returned no citable events for this axis"
    )
    return {
        "score": round(axis.score * 100, 1) if scored else None,
        "trend": round(axis.trend, 3) if axis.trend is not None else None,
        "trend_unit": TREND_UNIT_DIRECTION,
        "band": None,
        "confidence": round(axis.confidence, 2),
        "evidence_event_ids": ids,
        "live": True,
        **({"reason": reason} if reason else {}),
    }


def _rescale_axis(axis: dict) -> dict:
    """THE serializer for an authored axis dict -> the client's Axis. There is only one.

    There used to be two — this and `api.routers.companies._axis_to_client` — doing the
    same job with OPPOSITE behaviour on null, and the ranked list happened to use the
    one that coalesced. That is why this function is now shared rather than mirrored.

    Seeded axes are authored 0..1; the client's Axis is 0..100 in score units.

    A NULL score/band/trend STAYS NULL. `score or 0.0` turned "no observable evidence —
    not a zero, an absence" into a confident 0.0, which `_rank_key` then read as a real
    measurement (it tests isinstance(score, float)), promoting an unmeasured company into
    the fully-measured tier and sorting it dead last inside it — the exact inversion of
    the ranking policy. The client then rendered "0 ±0.0" on an axis whose own payload
    said it was an absence. Null is the only honest wire value for "we did not measure".

    `trend` is rescaled by ITS OWN unit, never by a factor inferred from `score`. The
    other serializer chose ×100-or-×1 by looking at whether *score* was ≤ 1.0 and then
    applied that to *trend*, so one unlabelled field carried two incompatible scales.
    An authored trend is a rate in the same 0..1 score units per 30 days, so it scales
    with the axis and SAYS SO; a fixture that authors a direction keeps its direction.
    Either way `trend_unit` is always emitted — a consumer must never have to guess
    which scale it received. See the unit history at TREND_UNIT_SCORE_PER_30D below.

    `live: False` — these numbers were authored, not computed this request.

    Receipts are whatever the fixture actually names, and NOTHING otherwise. This used
    to pad to `evidence_count` with empty strings, which rendered as that many clickable
    receipts that drilled into nothing on every company for two of the three axes. An
    axis that admits it has no receipts is worth more than a trace that dead-ends.
    Entries whose `event_ref` is missing contribute nothing, for the same reason.
    """

    def scaled(value, digits: int):
        # Only a real number scales. None -> None, and a non-numeric authored value is
        # an absence too rather than a crash or a silent zero.
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return round(float(value) * 100, digits)

    ids = [str(i) for i in (axis.get("evidence_event_ids") or []) if str(i).strip()]
    if not ids:
        ids = [
            str(e["event_ref"])
            for e in (axis.get("evidence") or [])
            if str(e.get("event_ref") or "").strip()
        ]

    raw_trend = axis.get("trend")
    if axis.get("trend_unit") == TREND_UNIT_DIRECTION:
        trend = round(float(raw_trend), 3) if isinstance(raw_trend, (int, float)) else None
        trend_unit = TREND_UNIT_DIRECTION
    else:
        trend = scaled(raw_trend, 2)
        trend_unit = TREND_UNIT_SCORE_PER_30D

    return {
        # The authored keys survive: the detail page's trace drawer joins on the inline
        # `evidence` spans, and dropping them would empty the drill-down.
        **axis,
        "score": scaled(axis.get("score"), 1),
        "band": scaled(axis.get("band"), 1),
        "trend": trend,
        "trend_unit": trend_unit,
        "confidence": axis.get("confidence") or 0.0,
        "evidence_event_ids": ids,
        "live": False,
        # The fixture's own stated reason outranks the generic one — "this is an absence,
        # not a zero" is the sentence this whole gate exists to preserve.
        **(
            {}
            if ids
            else {
                "reason": axis.get("reason")
                or "seeded axis — no screening computed for this axis yet"
            }
        ),
    }


# THE UNIT OF `trend`.
#
# memory.score runs its Kalman filter on dt in YEARS (_dt_years divides by 86400 then
# by 365.25, and _F is [[1, dt_years], [0, 1]]), so the velocity state — FounderScore
# .trend — is in score-units PER YEAR. This converts it to per-30-days, the horizon the
# UI's arrow claims to show.
#
# This was previously a bare `* 30`, which treated the rate as per-DAY and so displayed
# every trend 365.25x too large: Tensorpage rendered a 30-day momentum of 568.0 on a
# 0..100 axis, where the real figure is 1.56. Any number over ~100 here is definitionally
# impossible, which is how the unit error stayed visible for so long without being read
# as one.
_DAYS_PER_YEAR = 365.25
_TREND_YEARS_PER_30_DAYS = 30.0 / _DAYS_PER_YEAR

# `trend` does not mean the same thing on every axis, so every axis says which it is.
# The founder axis is a real rate from the Kalman filter; the screen axes are an LLM's
# directional call. Presenting both as one unlabelled number invites exactly the
# comparison neither supports.
TREND_UNIT_SCORE_PER_30D = "score_points_per_30d"
TREND_UNIT_DIRECTION = "direction_-1_to_1"

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


def _in_thesis_scope(row: dict, slug: str | None = None) -> bool:
    """S0 governs MEMBERSHIP, not score. A fund that does not invest in a sector does
    not rank it lower — it does not look at it. Unknown values stay in scope, because
    absent metadata must not quietly drop a founder (the Type 6 failure mode).

    `slug` is optional so existing single-row callers keep working; the list passes the
    one it already resolved rather than paying for it a second time.
    """
    from core import thesis as thesis_mod

    seeded = _fixture_row(slug if slug is not None else _slug_for_row(row)) or {}
    ok, _ = thesis_mod.in_scope(
        sector=seeded.get("sector"), stage=seeded.get("stage"), geo=seeded.get("geo")
    )
    return ok


def _backtest_cohort_names() -> frozenset[str]:
    from api.routers.deps import backtest_cohort_names

    return backtest_cohort_names()


def _fixture_index() -> dict[str, dict]:
    """slug -> seeded ranked-list entry, built once per mtime of the fixture file.

    `_fixture_row` used to re-read and re-parse companies.json and then linear-scan it,
    on EVERY call — and the list path calls it twice per company (thesis filter, then
    row build). At 27 companies that is 54 parses of a 13KB file per request; at 200 it
    is 400 parses plus a scan that is itself O(n), i.e. the quadratic term nobody put
    there on purpose. Keyed on mtime so editing a fixture still takes effect without a
    restart, which is why this is not a plain lru_cache.
    """
    from api.routers.deps import seed_dir

    path = seed_dir() / "companies.json"
    stamp = (str(path), path.stat().st_mtime_ns if path.exists() else 0)
    if _FIXTURE_INDEX.get("stamp") != stamp:
        entries = seed_or("companies", {}).get("companies", [])
        _FIXTURE_INDEX.clear()
        _FIXTURE_INDEX["stamp"] = stamp
        _FIXTURE_INDEX["by_slug"] = {e["id"]: e for e in entries if e.get("id")}
    return _FIXTURE_INDEX["by_slug"]


_FIXTURE_INDEX: dict = {}


def _fixture_row(slug: str | None) -> dict | None:
    """The seeded ranked-list entry for a slug, if one was authored."""
    return _fixture_index().get(slug) if slug else None


def _slug_for_row(row: dict) -> str | None:
    """The fixture slug for a company row WE ALREADY HAVE, with no database round trip.

    `deps.fixture_key(company_id)` exists for callers holding only an id: it calls
    `store.get_company(cid)` purely to learn the company's NAME, then maps name->slug.
    But the list path already fetched every column of every row in one bulk
    `all_companies()`, so re-fetching each row by id to read a field sitting in memory
    is a network round trip bought for nothing — and the list did it twice per company.
    Same mapping, same source of truth (`_slug_by_name`), zero queries.
    """
    from api.routers.deps import _slug_by_name

    name = row.get("name")
    cid = row.get("company_id")
    if not name:
        return str(cid) if cid else None
    return _slug_by_name().get(name, str(cid) if cid else None)


def _founder_ids_for_row(row: dict) -> list:
    """Founder entity ids from the row in hand, falling back to the event log.

    Mirrors `deps.founder_entity_ids`, minus its `store.get_company` — for the same
    reason as `_slug_for_row`: the column is already on the row. The event-log fallback
    is preserved exactly, because it is load-bearing (an unpopulated column otherwise
    scores every founder at the prior).
    """
    import json as _json
    from uuid import UUID

    from api.routers.deps import as_uuid

    raw = row.get("founder_entity_ids") or "[]"
    ids = _json.loads(raw) if isinstance(raw, str) else raw
    resolved = [u for u in (as_uuid(i) for i in ids) if u is not None]
    if resolved:
        return resolved

    from memory import store
    from schema.events import utcnow

    cid = as_uuid(row.get("company_id"))
    if cid is None:
        return []
    seen: dict[UUID, None] = {}
    for e in store.events(as_of=utcnow(), company_id=cid):
        if e.entity_id is not None:
            seen.setdefault(e.entity_id, None)
    return list(seen)


RANK_POLICY_ID = "min_axis_with_momentum_tiebreak"


def _rank_key(row: dict) -> tuple:
    """Rank by the WEAKEST of the three axes, then momentum. Never a blend.

    This is the policy `thesis.json` and `companies.json` both declare, and it is the
    one the no-blended-score design actually rests on: a company is only as investable
    as its weakest axis, and taking the minimum preserves that while an average would
    let a strong founder paper over a dead market.

    The previous implementation ranked on founder momentum alone — market and
    idea-vs-market were never consulted, so a fast-rising weak founder outranked a
    strong steady one and two published policy descriptions were both wrong.
    """
    axes = row.get("axes") or {}
    keys = ("founder", "market", "idea_vs_market")
    scores = [
        a.get("score")
        for a in (axes.get(k) for k in keys)
        if isinstance(a, dict) and isinstance(a.get("score"), (int, float))
    ]
    # An UNMEASURED axis must never become an advantage. Taking the min over only the
    # axes that happened to score would let a company measured on one strong axis
    # outrank a company measured on all three — "we could not look" would beat a real
    # weak reading. Companies missing an axis therefore sort below fully-measured ones
    # regardless of the scores they do have, and are ranked among themselves after that.
    # This is why `score` had to become nullable: the old 0.5 fallback made every
    # company look fully measured and hid this ordering question entirely.
    complete = len(scores) == len(keys)
    weakest = min(scores) if scores else 0.0
    momentum = (axes.get("founder") or {}).get("trend") or 0.0
    return (0 if complete else 1, -weakest, -momentum)
