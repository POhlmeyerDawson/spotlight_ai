"""The personal layer: derived lenses, personal rank beside core rank, per-company fit.

Owner: personalisation layer (docs/DIFFERENTIATOR.md §3).

Every route here requires a session, for the same reason `routers/profile.py` does: a
council without an owning profile is a council of nobody. Nothing in `api/main.py` or
`routers/companies.py` depends on this router, so a login outage costs the personal
ranking and leaves the objective one untouched (§1).

THE ONE THING TO NOT GET WRONG HERE (§0): the core order is READ from
`api.main.list_companies` and passed into the personal layer as an input. It is never
recomputed, adjusted or re-sorted on the way through. `/personal/rank` returns both
orders and the differences between them, because a system that can only show you your
own taste back is a mirror, not an analyst.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.auth import required_user
from api.routers.companies import dissent_was_served
from api.routers.deps import company_uuid, resolve_as_of
from intelligence import custom_council
from memory import profiles
from schema.vc import User

router = APIRouter(prefix="/personal", tags=["personal"])


@router.get("/lenses")
def get_lenses(user: User = Depends(required_user)) -> dict:
    """The council personas derived from this profile, and the ones we refused to invent.

    `not_derived` is served alongside, never instead: a client rendering only the derived
    lenses would present a two-lens council as a complete one.
    """
    derived = profiles.derive(user.user_id)
    lenses, not_derived = custom_council.derive_lenses(derived)
    return {
        "personalisation_enabled": derived.personalisation_enabled,
        "personalisation_reason": derived.personalisation_reason,
        "profile_confidence": derived.confidence,
        "lenses": [lens.model_dump(mode="json") for lens in lenses],
        "not_derived": [item.model_dump(mode="json") for item in not_derived],
        "min_lenses": custom_council.MIN_LENSES,
        "max_lenses": custom_council.MAX_LENSES,
        "sufficient": len(lenses) >= custom_council.MIN_LENSES,
    }


def _core_rows(as_of: datetime) -> list[dict]:
    """The objective ranked list, exactly as the public endpoint serves it.

    Imported lazily because `api.main` includes this router — and called rather than
    reimplemented, so the core order this layer re-ranks is definitionally the same one
    an unauthenticated client sees.
    """
    from api.main import list_companies

    return list_companies(as_of)


def _view(row: dict) -> custom_council.CompanyView | None:
    """One core row as the read-only view the personal layer is allowed to see.

    The API serves axes on 0..100; the personal layer works on the 0..1 scale
    `intelligence.screen` produces. An axis the core could not compute is OMITTED rather
    than defaulted to zero — the lens that wanted it abstains and says so.
    """
    cid = row.get("company_id")
    if not cid:
        return None
    axes, confidence, evidence = {}, {}, {}
    for name in custom_council.AXES:
        axis = (row.get("axes") or {}).get(name)
        if not isinstance(axis, dict) or not isinstance(axis.get("score"), (int, float)):
            continue
        axes[name] = float(axis["score"]) / 100.0
        confidence[name] = float(axis.get("confidence") or 0.0)
        evidence[name] = [
            UUID(str(value))
            for value in (axis.get("evidence_event_ids") or [])
            if _is_uuid(str(value))
        ]
    return custom_council.CompanyView(
        company_id=UUID(str(cid)),
        name=str(row.get("name") or ""),
        sector=(row.get("sector_key") or None),
        stage=(row.get("stage") or None),
        axes=axes,
        axis_confidence=confidence,
        axis_evidence=evidence,
    )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


@router.get("/rank")
def get_rank(as_of: datetime | None = None, user: User = Depends(required_user)) -> dict:
    """Core rank and personal rank, side by side, with the disagreements on top.

    No LLM call is made on this path. Thirteen companies times five personas is sixty-five
    completions for a list view, and the reading each lens produces is computed from the
    evidence rather than asked for — so the ranking is honest, auditable and fast, and the
    personas argue in full on the single-company route where the cost is worth paying.
    """
    cutoff = resolve_as_of(as_of)
    derived = profiles.derive(user.user_id)
    rows = _core_rows(cutoff)

    views = [view for view in (_view(row) for row in rows) if view is not None]
    core_order = [view.company_id for view in views]

    evidence_by_company: dict[UUID, list] = {}
    try:
        from memory import store

        for cid in core_order:
            evidence_by_company[cid] = custom_council.usable_evidence(
                cid, cutoff, store.events(company_id=cid, as_of=cutoff)
            )
    except Exception:  # noqa: BLE001 - an unreachable store costs evidence density, not the page
        evidence_by_company = {}

    ranking = custom_council.rank(views, core_order, derived, evidence_by_company, cutoff)
    return {
        **ranking.model_dump(mode="json"),
        # Served unconditionally, including when personalisation is off, so the client can
        # always show core rank beside personal rank — §3's mitigation against a council
        # that reproduces this VC's blind spots with machine authority.
        "core_rank": [
            {
                "company_id": str(view.company_id),
                "name": view.name,
                "core_rank": position,
            }
            for position, view in enumerate(views, 1)
        ],
    }


@router.get("/fit/{company_id}")
def get_fit(
    company_id: str,
    as_of: datetime | None = None,
    dissent_viewed: bool = False,
    user: User = Depends(required_user),
) -> dict:
    """One company read through this VC's council, on the same evidence as the memo.

    The recommendation is withheld until the dissent has ACTUALLY been served for this
    company — checked against server state via `companies.dissent_was_served`, never
    against the client's boolean. `dissent_viewed=true` on its own does nothing, which is
    the same enforcement `GET /companies/{id}/memo` already applies.
    """
    cutoff = resolve_as_of(as_of)
    cid = company_uuid(company_id)
    if cid is None:
        raise HTTPException(404, f"unknown company: {company_id}")

    derived = profiles.derive(user.user_id)
    if not derived.personalisation_enabled:
        raise HTTPException(
            409,
            {
                "error": "personalisation is off for this profile",
                "reason": derived.personalisation_reason,
                "core_rank_unaffected": True,
            },
        )

    lenses, not_derived = custom_council.derive_lenses(derived)
    if len(lenses) < custom_council.MIN_LENSES:
        raise HTTPException(
            409,
            {
                "error": f"only {len(lenses)} lens could be derived; "
                f"{custom_council.MIN_LENSES} are required",
                "not_derived": [item.model_dump(mode="json") for item in not_derived],
                "core_rank_unaffected": True,
            },
        )

    row = next(
        (item for item in _core_rows(cutoff) if str(item.get("company_id")) == str(cid)),
        {},
    )
    fit = custom_council.personal_fit(
        cid,
        cutoff,
        derived,
        sector=row.get("sector_key") or None,
        stage=row.get("stage") or None,
        name=str(row.get("name") or company_id),
        dissent_served=dissent_was_served(company_id) and dissent_viewed,
    )
    return {
        "company_id": company_id,
        **fit.model_dump(mode="json"),
        "lenses": [lens.model_dump(mode="json") for lens in lenses],
        "lenses_not_derived": [item.model_dump(mode="json") for item in not_derived],
    }
