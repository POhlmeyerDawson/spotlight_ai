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

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import required_user
from api.routers.companies import dissent_was_served
from api.routers.deps import company_uuid, resolve_as_of
from intelligence import custom_council
from memory import profiles
from schema.vc import AuthoredLensPatch, AuthoredLensWrite, User

router = APIRouter(prefix="/personal", tags=["personal"])


def _lens_uuid(lens_id: str) -> UUID:
    try:
        return UUID(lens_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, f"unknown council agent: {lens_id}") from None


def _reject_unreadable_quality(quality: str | None) -> None:
    """A quality with no readable term could never match anything on any company.

    Refused at the keyboard rather than stored: it would read 0.0 forever, occupy one of
    five council seats and dilute every other lens, which is a measurement the VC would
    reasonably believe was telling them something. The bounds in this feature refuse;
    they do not silently accept and quietly do nothing.
    """
    if quality is None:
        return
    if not custom_council.quality_terms(quality):
        raise HTTPException(
            422,
            {
                "error": f"the quality {quality!r} carries no readable term",
                "reason": (
                    "your agent reads this against the company's evidence graph, so a "
                    "quality made only of stopwords or single characters would score every "
                    "company 0.0 while still taking a council seat. Name the thing you are "
                    "looking for, e.g. 'security_engineering' or 'distribution'."
                ),
            },
        )


def _council_payload(user: User) -> dict:
    """The one shape every lens route returns, so a create and a delete hand back the
    same council the GET does and no client has to reconcile two views of it."""
    derived = profiles.derive(user.user_id)
    authored = profiles.list_authored_lenses(user.user_id)
    composed = custom_council.compose_council(derived, authored)
    derived_only, not_derived_alone = custom_council.derive_lenses(derived)
    return {
        "personalisation_enabled": derived.personalisation_enabled,
        "personalisation_reason": derived.personalisation_reason,
        "profile_confidence": derived.confidence,
        # THE OLD CONTRACT, UNCHANGED: `lenses` is the DERIVED half only, weighted among
        # themselves. A client that renders this list under a "derived" heading — which
        # the council builder does — keeps telling the truth.
        "lenses": [lens.model_dump(mode="json") for lens in derived_only],
        "not_derived": [item.model_dump(mode="json") for item in not_derived_alone],
        # The stored records the VC owns and edits.
        "authored": [record.model_dump(mode="json") for record in authored],
        # THE COUNCIL THAT ACTUALLY SCORES: derived and authored together, at the weights
        # the ranking uses. Every entry carries `origin`, so derived-vs-authored
        # provenance survives all the way to the screen.
        "council": [lens.model_dump(mode="json") for lens in composed.lenses],
        "council_not_derived": [item.model_dump(mode="json") for item in composed.not_derived],
        "weight_rule": composed.weight_rule,
        "refusal": composed.refusal.model_dump(mode="json") if composed.refusal else None,
        "min_lenses": custom_council.MIN_LENSES,
        "max_lenses": custom_council.MAX_LENSES,
        "sufficient": composed.refusal is None,
        # Stated on every read so it is never a surprise: a survey change recomputes the
        # derived lenses and cannot touch these.
        "authored_survive_rederive": True,
    }


@router.get("/lenses")
def get_lenses(user: User = Depends(required_user)) -> dict:
    """This VC's council: what the profile justified, what they wrote, and what we refused.

    `not_derived` is served alongside, never instead: a client rendering only the derived
    lenses would present a two-lens council as a complete one. `authored` and `council`
    are separate keys from `lenses` for the same reason — an authored agent must never be
    able to present itself as something the system read out of the profile.
    """
    return _council_payload(user)


@router.post("/lenses", status_code=201)
def create_lens(body: AuthoredLensWrite, user: User = Depends(required_user)) -> dict:
    """Author one council agent against this account.

    NOTHING IS CREATED IMPLICITLY. This is the only way a lens enters the authored table:
    an explicit request carrying a name, a quality, an argument, a weight and a statement
    of whether the VC typed it or knowingly accepted a template. There is no seeding, no
    default council and no "accept all templates" shortcut on the server.

    The ceiling REFUSES rather than clamps: the sixth agent is not stored and not silently
    dropped at scoring time, because a VC reasoning about a ranking produced by five of
    the six things they typed is worse off than one told they have too many.
    """
    _reject_unreadable_quality(body.quality)
    existing = profiles.list_authored_lenses(user.user_id)
    if len(existing) >= custom_council.MAX_LENSES:
        raise HTTPException(
            409,
            {
                "error": f"you already have {len(existing)} council agents and the ceiling "
                f"is {custom_council.MAX_LENSES}",
                "reason": (
                    "§3 puts the council at 3-5 personas; beyond that the weights get so "
                    "thin that no lens explains a ranking move. Nothing has been dropped — "
                    "delete an agent to make room."
                ),
                "max_lenses": custom_council.MAX_LENSES,
                "core_rank_unaffected": True,
            },
        )
    try:
        record = profiles.create_authored_lens(user.user_id, body)
    except ValueError as exc:
        raise HTTPException(409, {"error": str(exc)}) from None
    return {"created": record.model_dump(mode="json"), **_council_payload(user)}


@router.put("/lenses/{lens_id}")
def update_lens(
    lens_id: str, body: AuthoredLensPatch, user: User = Depends(required_user)
) -> dict:
    """Edit one agent. Partial — an omitted field is left alone rather than cleared.

    A derived lens has no route here on purpose: it is an inference, and the way to
    change it is to change the survey answers or the decision history it was read from.
    An editable "derived" lens would be an authored one wearing the system's name.
    """
    _reject_unreadable_quality(body.quality)
    try:
        record = profiles.update_authored_lens(user.user_id, _lens_uuid(lens_id), body)
    except ValueError as exc:
        raise HTTPException(409, {"error": str(exc)}) from None
    if record is None:
        raise HTTPException(404, f"unknown council agent: {lens_id}")
    return {"updated": record.model_dump(mode="json"), **_council_payload(user)}


@router.delete("/lenses/{lens_id}")
def delete_lens(lens_id: str, user: User = Depends(required_user)) -> dict:
    """Remove one agent. Permanent, and it stops scoring immediately."""
    if not profiles.delete_authored_lens(user.user_id, _lens_uuid(lens_id)):
        raise HTTPException(404, f"unknown council agent: {lens_id}")
    return {"deleted": lens_id, **_council_payload(user)}


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

    CONFIDENCE IS OMITTED ON THE SAME TERMS. `float(... or 0.0)` turned both a missing
    key and a genuine null into a measured zero, which is the one value the evidence-bar
    lens reads as "judged, and worth nothing". A confidence we do not have is left out
    of the dict so the mean is taken over the axes that actually reported one.
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
        raw_confidence = axis.get("confidence")
        if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool):
            confidence[name] = float(raw_confidence)
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

    ranking = custom_council.rank(
        views,
        core_order,
        derived,
        evidence_by_company,
        cutoff,
        # The authored council scores here too, or the builder would be a form that
        # writes to a table nothing reads.
        authored=profiles.list_authored_lenses(user.user_id),
    )
    return {
        **ranking.model_dump(mode="json"),
        "weight_rule": custom_council.WEIGHT_RULE,
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
    request: Request,
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

    authored = profiles.list_authored_lenses(user.user_id)
    composed = custom_council.compose_council(derived, authored)
    if composed.refusal is not None:
        raise HTTPException(
            409,
            {
                "error": composed.refusal.reason,
                "bound": composed.refusal.bound,
                "not_derived": [item.model_dump(mode="json") for item in composed.not_derived],
                "core_rank_unaffected": True,
            },
        )
    lenses, not_derived = composed.lenses, composed.not_derived

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
        dissent_served=dissent_was_served(company_id, request) and dissent_viewed,
        authored=authored,
    )
    return {
        "company_id": company_id,
        **fit.model_dump(mode="json"),
        "lenses": [lens.model_dump(mode="json") for lens in lenses],
        "lenses_not_derived": [item.model_dump(mode="json") for item in not_derived],
        "weight_rule": composed.weight_rule,
    }
