"""The custom VC council — personas instantiated from a VC profile (DIFFERENTIATOR §3).

THE ARCHITECTURAL RULE (§0), which every function here is arranged around:

    The personal layer sits ON TOP of the core rank. It never modifies the core score.

Two VCs must see the same objective truth and a different ranking. If preference moved
the core score, the same founder would be *more capable* at a bolder fund, which is
nonsense — the thesis engine already avoids exactly this by moving the evidence bar
rather than the score. So nothing in this module writes to a `ScreeningResult`, emits an
`Event`, or feeds back into `memory.score`. It reads the core numbers, reweights them
through lenses that are attributable to profile fields, and reports both orderings side
by side.

THE SECOND RULE, inherited from the dissent engine's bug: the council runs on the SAME
evidence graph as the core analysis, filtered by `intelligence.flags.is_impeached` — the
same filter every other module uses. The bear case was once blind to 20-25% of what the
memo could see, so bull and bear argued about different facts. The council gets no
private evidence, and nothing is withheld from it.

THE THIRD RULE, inherited from `memory/profiles.py`: a lens with no derivable
justification is NOT invented. If the profile supports two lenses, two lenses are
produced and `lenses_not_derived` says which were skipped and why. A lens that cannot
read a company (an unknown sector against a sector prior) ABSTAINS visibly and its
weight is redistributed — it never contributes a silent 0.0, which would be a penalty
dressed up as a measurement.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from core import llm
from intelligence import council, flags
from schema.events import AntiMemo, Event, EventKind, ScreeningResult
from schema.vc import DerivedProfile, NotInferred, Provenance

Judge = Callable[..., str | dict]

AXES = ("founder", "market", "idea_vs_market")

#: A council of one is not a council, and a single lens is just a renamed axis. Below
#: this the personal layer refuses to produce a fit score and says so.
MIN_LENSES = 2

#: §3 says 3-5 personas. Five is the ceiling; the floor is whatever the profile earns.
MAX_LENSES = 5

#: Receipts in the filtered packet at which evidence density is considered saturated.
#: Used only by the evidence-bar lens, which is the one lens that reads volume at all.
EVIDENCE_SATURATION = 12

#: How far personal rank must move from core rank before the divergence is a headline
#: rather than noise. Three places on a thirteen-company list is a different shortlist.
DIVERGENCE_HEADLINE = 3


class LensKind(StrEnum):
    FOUNDER_BET = "founder_bet"
    MARKET_BET = "market_bet"
    CONTRARIAN_TIMING = "contrarian_timing"
    EVIDENCE_BAR = "evidence_bar"
    SECTOR_PATTERN = "sector_pattern"
    STAGE_PATTERN = "stage_pattern"
    RED_LINE_AUDITOR = "red_line_auditor"


#: Each persona is the system prompt the lens argues under when narration is on. They
#: are deliberately different objectives, not tones: three personas that differ only in
#: adjective produce three identical readings, which is the decorative failure this
#: module is written against.
_PERSONAS: dict[LensKind, str] = {
    LensKind.FOUNDER_BET: (
        "You back people. Argue from the evidence about what this founder has actually "
        "built and shipped. Market conditions are somebody else's argument."
    ),
    LensKind.MARKET_BET: (
        "You back markets. Argue from the evidence about demand, pull and timing. A "
        "remarkable founder in a dead market is still a dead market."
    ),
    LensKind.CONTRARIAN_TIMING: (
        "You back non-consensus theses. Argue from the evidence about whether this is a "
        "genuinely different bet or a crowded one, and whether it is early or wrong."
    ),
    LensKind.EVIDENCE_BAR: (
        "You police the evidence bar this fund actually operates at. Argue about whether "
        "what is on the table would clear it, not about whether the company is good."
    ),
    LensKind.SECTOR_PATTERN: (
        "You read this company against where this fund's money has actually gone. State "
        "whether it sits inside the revealed pattern or outside it. Outside is a finding, "
        "not a verdict."
    ),
    LensKind.STAGE_PATTERN: (
        "You read this company against the stages this fund has actually written cheques "
        "at. State whether it fits that revealed range."
    ),
    LensKind.RED_LINE_AUDITOR: (
        "You check the fund's red lines against the evidence. A red line that fires is a "
        "veto regardless of score. Only fire on evidence, never on a hunch."
    ),
}

_NARRATION_PROMPT = (
    "Return JSON with rationale (nonempty string, one or two sentences, specific to the "
    "supplied evidence) and evidence_event_ids (only ids that appear in the packet). "
    "Argue only your own lens. Do not restate the other lenses and do not average "
    "anything."
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Lens(BaseModel):
    """One council persona, its weight, and the profile fields that justified it.

    `justified_by` is not decoration. A lens that cannot name the profile field it came
    from is a preference we invented on the user's behalf, and §3's whole claim is that
    every personal adjustment shows its lens and its weight.
    """

    kind: LensKind
    persona: str
    weight: float = Field(ge=0.0, le=1.0)
    justified_by: list[str] = Field(min_length=1)
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _justified(self) -> Lens:
        if not all(field.strip() for field in self.justified_by):
            raise ValueError("a lens must name the profile field that justified it")
        return self


class LensContribution(BaseModel):
    """What one lens read off the shared evidence, and what it added to the fit score.

    `contribution == weight * reading` for every lens that read, and the fit score is
    their sum. An abstaining lens carries `reading=None`, `contribution=0.0` and a
    reason — it is excluded from the normalisation rather than scored as zero.
    """

    lens: LensKind
    weight: float = Field(ge=0.0, le=1.0)
    reading: float | None = Field(default=None, ge=0.0, le=1.0)
    contribution: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    company_facts_used: list[str] = Field(default_factory=list)
    provenance: Provenance
    abstained_reason: str | None = None

    @model_validator(mode="after")
    def _abstention_is_explicit(self) -> LensContribution:
        if not self.rationale.strip():
            raise ValueError("a contribution must say what it read")
        if (self.reading is None) != bool(self.abstained_reason):
            raise ValueError("an abstaining lens must carry a reason, and only it may")
        if self.reading is None and self.contribution != 0.0:
            raise ValueError("an abstaining lens contributes nothing")
        return self


class FounderMarketFit(BaseModel):
    """Founder-market fit read through THIS VC's thesis, not in the abstract.

    `caveats` carries what the profile could not condition on. A founder-market fit that
    silently drops its sector term reads as a stronger finding than it is.
    """

    score: float = Field(ge=0.0, le=1.0)
    assessment: str
    read_through: list[str] = Field(min_length=1)
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class RedLineHit(BaseModel):
    statement: str
    matched_on: str
    source: str
    confidence: float = Field(ge=0.0, le=1.0)


class CompanyView(BaseModel):
    """The core-layer facts the personal layer is allowed to read.

    Deliberately a copy, and deliberately read-only: the personal layer cannot reach a
    `ScreeningResult` from here, so it structurally cannot move a core score. Axes are
    0..1, the scale `intelligence.screen` produces — callers holding the API's 0..100
    rows convert on the way in.
    """

    company_id: UUID
    name: str = ""
    sector: str | None = None
    stage: str | None = None
    axes: dict[str, float] = Field(default_factory=dict)
    axis_confidence: dict[str, float] = Field(default_factory=dict)
    axis_evidence: dict[str, list[UUID]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bounded(self) -> CompanyView:
        for name, value in self.axes.items():
            if name not in AXES:
                raise ValueError(f"unknown axis: {name}")
            self.axes[name] = max(0.0, min(1.0, float(value)))
        return self


class PersonalFit(BaseModel):
    """The personal read on one company. Never a replacement for the core read.

    `core_axes` is echoed verbatim so a client can show core beside personal, and so a
    test can assert the personal layer returned the core numbers unmodified.
    """

    company_id: UUID
    as_of: datetime
    fit_score: float = Field(ge=0.0, le=1.0)
    contributions: list[LensContribution]
    founder_market_fit: FounderMarketFit
    red_line_hits: list[RedLineHit] = Field(default_factory=list)
    core_axes: dict[str, float]
    core_weakest_axis: str
    core_weakest_score: float
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    #: The council's recommendation, withheld until a bear case has been served.
    personal_recommendation: council.CouncilDecision | None = None
    recommendation_locked_reason: str | None = None
    anti_memo: AntiMemo | None = None

    @model_validator(mode="after")
    def _lock_and_arithmetic(self) -> PersonalFit:
        if (self.personal_recommendation is None) != bool(self.recommendation_locked_reason):
            raise ValueError("a withheld recommendation must carry a reason, and only it may")
        if self.personal_recommendation is not None and self.anti_memo is None:
            raise ValueError("a recommendation cannot be served without the bear case behind it")
        if not self.contributions:
            raise ValueError("a fit score with no lens behind it is not attributable")
        total = sum(item.contribution for item in self.contributions)
        if abs(total - self.fit_score) > 1e-6:
            raise ValueError("fit_score must equal the sum of its lens contributions")
        return self


class PersonalRankRow(BaseModel):
    company_id: UUID
    name: str
    core_rank: int
    personal_rank: int
    fit_score: float = Field(ge=0.0, le=1.0)
    core_weakest_score: float
    #: Positive means the personal layer PROMOTED the company relative to core.
    divergence: int
    top_lens: LensKind | None = None
    why: str


class Disagreement(BaseModel):
    company_id: UUID
    name: str
    core_rank: int
    personal_rank: int
    divergence: int
    explanation: str


class PersonalRanking(BaseModel):
    """Core rank and personal rank, side by side, with the disagreements on top.

    §3's mitigation, made structural: a council tuned to a VC's history reproduces that
    VC's blind spots with machine authority, so the disagreements are the headline
    output and agreement is only ever confirmation.
    """

    as_of: datetime
    personalised: bool
    reason: str
    lenses: list[Lens] = Field(default_factory=list)
    lenses_not_derived: list[NotInferred] = Field(default_factory=list)
    rows: list[PersonalRankRow] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reason_always(self) -> PersonalRanking:
        if not self.reason.strip():
            raise ValueError("the ranking must always say whether it is personalised and why")
        if not self.personalised and self.rows:
            raise ValueError("an unpersonalised ranking must not publish a personal order")
        return self


# ---------------------------------------------------------------------------
# The shared evidence filter
# ---------------------------------------------------------------------------


def usable_evidence(company_id: UUID, as_of: datetime, events: list[Event]) -> list[Event]:
    """The company's evidence graph, filtered exactly as the memo and the dissent filter it.

    The predicate must stay identical to `intelligence.council._packet` and
    `intelligence.dissent.generate_from_evidence`: same as_of scope, INTEGRITY events
    excluded as bookkeeping, and `flags.is_impeached` — never a blanket test on
    `integrity_flags`, which would void the entire non-Latin-script cohort.

    `tests/test_custom_council.py::test_council_and_custom_council_see_identical_evidence`
    asserts this returns the same set the core council builds its packet from, so the
    two cannot drift apart the way the bull and bear once did.
    """
    return [
        event
        for event in events
        if event.company_id == company_id
        and event.observed_at <= as_of
        and event.kind != EventKind.INTEGRITY
        and not flags.is_impeached(event)
    ]


# ---------------------------------------------------------------------------
# Lens derivation — §3, "each lens is a persona + a weight + the profile fields
# that justified it"
# ---------------------------------------------------------------------------


def _axis_lens(
    kind: LensKind, axis: str, profile: DerivedProfile
) -> tuple[float, Lens] | tuple[float, None]:
    weights = profile.axis_weights_stated
    if weights is None:
        return 0.0, None
    share = float(getattr(weights, axis))
    if share <= 0.0:
        return 0.0, None
    return share, Lens(
        kind=kind,
        persona=_PERSONAS[kind],
        weight=share,
        justified_by=[f"axis_weights_stated.{axis}"],
        provenance=weights.provenance,
        confidence=weights.confidence,
    )


def _evidence_bar_lens(profile: DerivedProfile) -> tuple[float, Lens | None]:
    """Justified by conviction style. Stated is preferred; revealed stands in when the
    survey is silent, because a conviction style read off actual cheques is still a real
    observation about this VC — it is simply a different basis, and the provenance says so.
    """
    style = profile.conviction_style_stated or profile.conviction_style_revealed
    if style is None:
        return 0.0, None
    field = (
        "conviction_style_stated"
        if profile.conviction_style_stated is not None
        else "conviction_style_revealed"
    )
    # A VC with no lean has no evidence-bar argument to make; a VC with a strong lean
    # has a loud one. Weighting by |score| makes the lens's prominence a measurement
    # rather than a constant.
    raw = 0.15 + 0.35 * abs(style.score)
    return raw, Lens(
        kind=LensKind.EVIDENCE_BAR,
        persona=_PERSONAS[LensKind.EVIDENCE_BAR],
        weight=raw,
        justified_by=[f"{field}.score={style.score} ({style.label})"],
        provenance=style.provenance,
        confidence=style.confidence,
    )


def _prior_lens(kind: LensKind, field: str, profile: DerivedProfile) -> tuple[float, Lens | None]:
    """A revealed-concentration lens. Its weight IS the concentration: a fund spread
    evenly over five sectors has a weak sector argument, a fund with 80% in one has a
    loud one. A prior list that exists but says nothing is not a lens.
    """
    priors = getattr(profile, field)
    if not priors:
        return 0.0, None
    top = priors[0]
    return float(top.share), Lens(
        kind=kind,
        persona=_PERSONAS[kind],
        weight=float(top.share),
        justified_by=[f"{field}[0]={top.key} ({top.count} of the invested rows)"],
        provenance=top.provenance,
        confidence=min(1.0, top.count / 5.0),
    )


def _red_line_lens(profile: DerivedProfile) -> tuple[float, Lens | None]:
    if not profile.red_lines:
        return 0.0, None
    strongest = max(profile.red_lines, key=lambda line: line.confidence)
    return 0.4 * strongest.confidence, Lens(
        kind=LensKind.RED_LINE_AUDITOR,
        persona=_PERSONAS[LensKind.RED_LINE_AUDITOR],
        weight=0.4 * strongest.confidence,
        justified_by=[f"red_lines[{len(profile.red_lines)}] strongest={strongest.statement!r}"],
        provenance=strongest.provenance,
        confidence=strongest.confidence,
    )


_MISSING_REASONS: dict[LensKind, tuple[str, str]] = {
    LensKind.FOUNDER_BET: (
        "lens:founder_bet",
        "needs axis_weights_stated.founder — the survey did not produce a founder weight",
    ),
    LensKind.MARKET_BET: (
        "lens:market_bet",
        "needs axis_weights_stated.market — the survey did not produce a market weight",
    ),
    LensKind.CONTRARIAN_TIMING: (
        "lens:contrarian_timing",
        "needs axis_weights_stated.idea_vs_market — the survey did not produce one",
    ),
    LensKind.EVIDENCE_BAR: (
        "lens:evidence_bar",
        "needs a conviction style on either side; neither the survey nor the decision "
        "history produced one",
    ),
    LensKind.SECTOR_PATTERN: (
        "lens:sector_pattern",
        "needs sector_priors — too few invested rows carry a sector to read a concentration",
    ),
    LensKind.STAGE_PATTERN: (
        "lens:stage_pattern",
        "needs stage_priors — too few invested rows carry a stage to read a concentration",
    ),
    LensKind.RED_LINE_AUDITOR: (
        "lens:red_line_auditor",
        "no stated red lines and no unanimous pass pattern strong enough to raise a candidate",
    ),
}


def derive_lenses(profile: DerivedProfile) -> tuple[list[Lens], list[NotInferred]]:
    """3-5 personas derived from the profile, plus the ones we refused to invent.

    Every candidate lens is gated on a specific profile field. There is no default lens
    and no filler: if the profile supports two, two come back and the other five are
    listed in `not_derived` with the field they would have needed. That is the same
    discipline `memory/profiles.py` applies to `axis_weights_revealed`.

    Weights are the RAW justifications (an axis share, a conviction magnitude, a sector
    concentration) normalised over whichever lenses survived selection — so two profiles
    with different survey answers get genuinely different weight vectors rather than
    three equal thirds.
    """
    candidates: list[tuple[float, Lens | None, LensKind]] = [
        (*_axis_lens(LensKind.FOUNDER_BET, "founder", profile), LensKind.FOUNDER_BET),
        (*_axis_lens(LensKind.MARKET_BET, "market", profile), LensKind.MARKET_BET),
        (
            *_axis_lens(LensKind.CONTRARIAN_TIMING, "idea_vs_market", profile),
            LensKind.CONTRARIAN_TIMING,
        ),
        (*_evidence_bar_lens(profile), LensKind.EVIDENCE_BAR),
        (*_prior_lens(LensKind.SECTOR_PATTERN, "sector_priors", profile), LensKind.SECTOR_PATTERN),
        (*_prior_lens(LensKind.STAGE_PATTERN, "stage_priors", profile), LensKind.STAGE_PATTERN),
        (*_red_line_lens(profile), LensKind.RED_LINE_AUDITOR),
    ]

    not_derived = [
        NotInferred(field_name=_MISSING_REASONS[kind][0], reason=_MISSING_REASONS[kind][1])
        for raw, lens, kind in candidates
        if lens is None or raw <= 0.0
    ]
    derivable = [(raw, lens) for raw, lens, _ in candidates if lens is not None and raw > 0.0]

    # Ties are broken by kind name so the selection is deterministic across runs — a
    # ranking that reshuffles on identical input is not a ranking.
    derivable.sort(key=lambda item: (-item[0], item[1].kind.value))
    kept, dropped = derivable[:MAX_LENSES], derivable[MAX_LENSES:]
    for raw, lens in dropped:
        not_derived.append(
            NotInferred(
                field_name=f"lens:{lens.kind.value}",
                reason=(
                    f"derivable (raw weight {round(raw, 4)}) but outside the {MAX_LENSES}-lens "
                    "ceiling; the profile justified stronger lenses"
                ),
            )
        )

    total = sum(raw for raw, _ in kept)
    if total <= 0.0:
        return [], not_derived
    lenses = [lens.model_copy(update={"weight": round(raw / total, 6)}) for raw, lens in kept]
    return lenses, not_derived


# ---------------------------------------------------------------------------
# Lens readings — the discriminating part
# ---------------------------------------------------------------------------


def _weakest(view: CompanyView) -> tuple[str, float]:
    """The core's own ranking key: a company is only as investable as its weakest axis."""
    present = {name: view.axes[name] for name in AXES if name in view.axes}
    if not present:
        return "founder", 0.0
    name = min(present, key=lambda key: (present[key], key))
    return name, present[name]


def _evidence_bar_reading(
    view: CompanyView, evidence: list[Event], profile: DerivedProfile
) -> tuple[float, str]:
    """Where this fund's evidence bar puts the company, on a line between two readings.

    An evidence-heavy investor is bounded by the WEAKEST axis and discounts it by how
    much evidence actually exists — they will not pay for a gap. A conviction-heavy
    investor backs the STRONGEST axis and tolerates the gaps around it. Interpolating
    between those two by the conviction score is what makes this lens read the same
    company differently for two different funds, which is the entire point of the
    feature: the number moves because the VC moved, not because the founder did.
    """
    style = profile.conviction_style_stated or profile.conviction_style_revealed
    position = 0.5 if style is None else (float(style.score) + 1.0) / 2.0

    present = [view.axes[name] for name in AXES if name in view.axes]
    weakest = min(present) if present else 0.0
    strongest = max(present) if present else 0.0

    density = min(1.0, len(evidence) / EVIDENCE_SATURATION)
    confidences = [view.axis_confidence.get(name) for name in AXES]
    known = [float(value) for value in confidences if isinstance(value, (int, float))]
    mean_confidence = sum(known) / len(known) if known else 0.0
    sufficiency = 0.5 * density + 0.5 * mean_confidence

    reading = (1.0 - position) * (weakest * sufficiency) + position * strongest
    label = "conviction-heavy" if position > 0.5 else "evidence-heavy"
    return max(0.0, min(1.0, reading)), (
        f"Read at a {label} bar (conviction position {round(position, 2)}): weakest axis "
        f"{round(weakest, 3)} discounted by evidence sufficiency {round(sufficiency, 3)} "
        f"({len(evidence)} usable receipts, mean axis confidence "
        f"{round(mean_confidence, 2)}), strongest axis {round(strongest, 3)}."
    )


def _prior_reading(
    priors: list, key: str | None, noun: str
) -> tuple[float | None, str, str | None]:
    """(reading, rationale, abstained_reason) for a revealed-concentration lens."""
    if not key or not key.strip():
        return (
            None,
            f"This company carries no {noun}, so the fund's revealed {noun} pattern cannot "
            f"be applied to it.",
            f"the company record has no {noun}; scoring it 0 would penalise missing "
            f"metadata rather than measure fit",
        )
    normalised = key.strip().lower()
    for prior in priors:
        if prior.key == normalised:
            return (
                float(prior.share),
                f"{noun.capitalize()} {normalised!r} is {round(prior.share * 100)}% of this "
                f"fund's invested rows ({prior.count} of them).",
                None,
            )
    return (
        0.0,
        f"This fund has never invested in {noun} {normalised!r}. That is a divergence from "
        f"the revealed pattern, not evidence about the company.",
        None,
    )


_STOPWORDS = frozenset(
    {
        "a", "an", "and", "any", "are", "at", "be", "by", "every", "for", "from", "in",
        "is", "it", "line", "no", "not", "of", "on", "or", "possible", "red", "regardless",
        "seen", "so", "that", "the", "this", "to", "was", "we", "who", "will", "with",
        "far", "passed", "score", "==", "never",
    }
)


def _red_line_reading(
    profile: DerivedProfile, view: CompanyView, evidence: list[Event]
) -> tuple[float, str, list[RedLineHit]]:
    """Red lines fire on a whole-word match against the company's sector, stage or its
    evidence text — never on a hunch, and never on a substring (a red line on "ai" must
    not fire on "detail"). Only STATED red lines can veto: a `revealed_candidate` is a
    pattern the user has not confirmed, so it is surfaced as a hit at its own confidence
    and weighted by it rather than treated as a rule this VC holds.
    """
    haystack = " ".join(
        [
            (view.sector or ""),
            (view.stage or ""),
            *[
                " ".join(
                    [event.evidence_span or ""]
                    + [
                        value
                        for key in ("claim", "title", "text", "body", "description")
                        for value in [event.payload.get(key)]
                        if isinstance(value, str)
                    ]
                )
                for event in evidence
            ],
        ]
    ).lower()
    words = set(_tokens(haystack))

    hits: list[RedLineHit] = []
    for line in profile.red_lines:
        terms = [term for term in _tokens(line.statement.lower()) if term not in _STOPWORDS]
        matched = sorted(set(terms) & words)
        if not matched:
            continue
        hits.append(
            RedLineHit(
                statement=line.statement,
                matched_on=", ".join(matched),
                source=line.source,
                confidence=line.confidence,
            )
        )

    if not hits:
        return (
            1.0,
            f"None of the {len(profile.red_lines)} red line(s) on this profile match the "
            f"company's sector, stage or evidence text.",
            [],
        )
    # A stated red line at confidence 1.0 drives the reading to 0. A revealed candidate
    # at 0.4 drives it to 0.6 — a flag, not a veto.
    strongest = max(hit.confidence for hit in hits)
    return (
        max(0.0, 1.0 - strongest),
        "; ".join(f"{hit.source} red line {hit.statement!r} matched on {hit.matched_on}" for hit in hits),
        hits,
    )


def _tokens(text: str) -> list[str]:
    return [chunk for chunk in "".join(c if c.isalnum() else " " for c in text).split() if chunk]


# ---------------------------------------------------------------------------
# Narration — the persona actually arguing over the shared packet
# ---------------------------------------------------------------------------


def _narrate(
    lens: Lens, packet: list[dict], valid_ids: set[str], judge: Judge
) -> tuple[str | None, list[UUID]]:
    """Run one persona over the SAME packet the core council uses.

    Narration never changes the reading. The number is computed from the evidence and is
    auditable without a model in the loop; the persona supplies the argument and its
    receipts. Letting an LLM set the score would put a confident, undiscriminating float
    at the centre of the feature — which is the failure this codebase has already shipped
    more than once.
    """
    try:
        raw = judge(
            _NARRATION_PROMPT,
            system=lens.persona,
            tier="deep",
            untrusted=json.dumps(packet),
            json_mode=True,
        )
        data = raw if isinstance(raw, dict) else json.loads(raw)
        rationale = data["rationale"]
        cited = data["evidence_event_ids"]
        if not isinstance(rationale, str) or not rationale.strip() or not isinstance(cited, list):
            raise ValueError("malformed lens narration")
        receipts = [UUID(str(value)) for value in cited if str(value) in valid_ids]
        if not receipts:
            raise ValueError("lens narration has no valid receipts")
        return rationale.strip(), list(dict.fromkeys(receipts))
    except Exception:
        # Silent degradation to the computed rationale. The lens still reports a real
        # reading and real receipts; it just argues in the system's own words.
        return None, []


# ---------------------------------------------------------------------------
# Scoring one company
# ---------------------------------------------------------------------------


def _contribution(
    lens: Lens,
    view: CompanyView,
    evidence: list[Event],
    profile: DerivedProfile,
) -> tuple[LensContribution, list[RedLineHit]]:
    facts: list[str] = []
    receipts: list[UUID] = []
    hits: list[RedLineHit] = []
    abstained: str | None = None

    if lens.kind in (LensKind.FOUNDER_BET, LensKind.MARKET_BET, LensKind.CONTRARIAN_TIMING):
        axis = {
            LensKind.FOUNDER_BET: "founder",
            LensKind.MARKET_BET: "market",
            LensKind.CONTRARIAN_TIMING: "idea_vs_market",
        }[lens.kind]
        if axis not in view.axes:
            reading, rationale = None, (
                f"The core screen produced no {axis} axis for this company, so this lens "
                f"has nothing of its own to read."
            )
            abstained = f"no {axis} axis was computed for this company"
        else:
            reading = view.axes[axis]
            rationale = (
                f"Reads the {axis} axis at {round(reading, 3)} and weights it at "
                f"{round(lens.weight, 3)} because this profile puts "
                f"{round(lens.weight * 100)}% of its lens weight here."
            )
            receipts = list(view.axis_evidence.get(axis, []))

    elif lens.kind == LensKind.EVIDENCE_BAR:
        reading, rationale = _evidence_bar_reading(view, evidence, profile)
        receipts = [event.event_id for event in evidence[:5]]

    elif lens.kind == LensKind.SECTOR_PATTERN:
        reading, rationale, abstained = _prior_reading(profile.sector_priors, view.sector, "sector")
        facts = ["sector"]

    elif lens.kind == LensKind.STAGE_PATTERN:
        reading, rationale, abstained = _prior_reading(profile.stage_priors, view.stage, "stage")
        facts = ["stage"]

    else:  # RED_LINE_AUDITOR
        reading, rationale, hits = _red_line_reading(profile, view, evidence)
        facts = ["sector", "stage"]
        receipts = [event.event_id for event in evidence[:3]]

    contribution = 0.0 if reading is None else round(lens.weight * reading, 6)
    return (
        LensContribution(
            lens=lens.kind,
            weight=lens.weight,
            reading=None if reading is None else round(reading, 6),
            contribution=contribution,
            rationale=rationale,
            evidence_event_ids=receipts,
            company_facts_used=facts,
            provenance=lens.provenance,
            abstained_reason=abstained,
        ),
        hits,
    )


def _renormalise(contributions: list[LensContribution]) -> list[LensContribution]:
    """Redistribute an abstaining lens's weight over the lenses that actually read.

    Without this, a company with no recorded sector would be silently penalised by the
    sector lens's whole weight — a missing-metadata penalty presented as a fit score,
    which is exactly the Type 6 failure mode in a new costume.
    """
    live = [item for item in contributions if item.reading is not None]
    total = sum(item.weight for item in live)
    if not live or total <= 0.0:
        return contributions
    out: list[LensContribution] = []
    for item in contributions:
        if item.reading is None:
            out.append(item)
            continue
        weight = round(item.weight / total, 6)
        out.append(
            item.model_copy(
                update={
                    "weight": weight,
                    "contribution": round(weight * item.reading, 6),
                }
            )
        )
    return out


def _founder_market_fit(
    view: CompanyView, profile: DerivedProfile, lenses: list[Lens], evidence: list[Event]
) -> FounderMarketFit:
    """The founder axis, conditioned on where this fund's money has actually gone.

    Deliberately NOT a second opinion on the founder: the founder axis is the objective
    layer's answer and this does not touch it. What the thesis adds is the market half of
    "founder-market fit" — whether the market this founder chose is one this fund has
    conviction in. Where the profile carries no sector prior, the conditioning is dropped
    and the caveat says so, rather than defaulting the multiplier to 1.0 and quietly
    presenting an unconditioned founder score as a fit assessment.
    """
    base = view.axes.get("founder")
    read_through = [f"lens:{lens.kind.value}" for lens in lenses]
    caveats: list[str] = []

    if base is None:
        return FounderMarketFit(
            score=0.0,
            assessment=(
                "Founder-market fit cannot be assessed: the core screen produced no founder "
                "axis for this company, and inventing one would be a claim about a person "
                "we hold no evidence about."
            ),
            read_through=read_through or ["none"],
            caveats=["no founder axis"],
        )

    reading, _, sector_abstained = _prior_reading(profile.sector_priors, view.sector, "sector")
    if not profile.sector_priors:
        caveats.append(
            "no sector prior on this profile, so the market half of founder-market fit is "
            "unconditioned — this is the founder axis read through the lens weights alone"
        )
        alignment = None
    elif sector_abstained:
        caveats.append(f"sector conditioning unavailable: {sector_abstained}")
        alignment = None
    else:
        alignment = reading

    score = base if alignment is None else base * (0.6 + 0.4 * alignment)
    sector_note = (
        "with no sector conditioning available"
        if alignment is None
        else (
            f"in {view.sector!r}, which is {round(alignment * 100)}% of this fund's "
            f"invested rows"
        )
    )
    heaviest = max(lenses, key=lambda lens: lens.weight) if lenses else None
    assessment = (
        f"Founder axis {round(base, 3)} {sector_note}. This fund's heaviest lens is "
        f"{heaviest.kind.value if heaviest else 'none'} at "
        f"{round(heaviest.weight, 3) if heaviest else 0.0}, so the fit is read primarily "
        f"through {heaviest.justified_by[0] if heaviest else 'no derivable preference'}."
    )
    return FounderMarketFit(
        score=max(0.0, min(1.0, score)),
        assessment=assessment,
        read_through=read_through or ["none"],
        evidence_event_ids=list(view.axis_evidence.get("founder", []))
        or [event.event_id for event in evidence[:3]],
        caveats=caveats,
    )


def score_company(
    view: CompanyView,
    lenses: list[Lens],
    profile: DerivedProfile,
    evidence: list[Event],
    as_of: datetime,
    *,
    judge: Judge | None = None,
    anti_memo: AntiMemo | None = None,
    dissent_served: bool = False,
    core_decision: council.CouncilDecision | None = None,
) -> PersonalFit:
    """One company's personal read, over evidence the caller has already filtered.

    `evidence` must come from `usable_evidence` — the same graph, the same filter. The
    core axes are echoed into the result untouched; nothing here writes back.

    The recommendation is withheld unless a bear case has ACTUALLY been served AND the
    council was non-empty. An empty council unlocking the recommendation is the bug that
    was fixed in `api/routers/companies.py::run_council`, and it is not reintroduced here.
    """
    if len(lenses) < MIN_LENSES:
        raise ValueError(f"a personal fit needs at least {MIN_LENSES} derivable lenses")

    contributions: list[LensContribution] = []
    red_line_hits: list[RedLineHit] = []
    for lens in lenses:
        contribution, hits = _contribution(lens, view, evidence, profile)
        contributions.append(contribution)
        red_line_hits.extend(hits)

    contributions = _renormalise(contributions)

    if judge is not None:
        packet, valid_ids = _packet(view, evidence, as_of)
        narrated: list[LensContribution] = []
        for lens, contribution in zip(lenses, contributions):
            rationale, receipts = _narrate(lens, packet, valid_ids, judge)
            narrated.append(
                contribution
                if rationale is None
                else contribution.model_copy(
                    update={
                        "rationale": f"{rationale} [computed: {contribution.rationale}]",
                        "evidence_event_ids": receipts or contribution.evidence_event_ids,
                    }
                )
            )
        contributions = narrated

    fit_score = round(sum(item.contribution for item in contributions), 6)
    weakest_axis, weakest_score = _weakest(view)

    recommendation, locked_reason = None, "open the dissent view first"
    if dissent_served and anti_memo is not None and str(anti_memo.bear_case or "").strip():
        if any(item.reading is not None for item in contributions):
            recommendation = core_decision or council.CouncilDecision.PROOF_PROTOCOL
            locked_reason = None
        else:
            locked_reason = (
                "every derived lens abstained on this company, so the council argued "
                "nothing — an empty council does not unlock a recommendation"
            )

    return PersonalFit(
        company_id=view.company_id,
        as_of=as_of,
        fit_score=max(0.0, min(1.0, fit_score)),
        contributions=contributions,
        founder_market_fit=_founder_market_fit(view, profile, lenses, evidence),
        red_line_hits=red_line_hits,
        # Echoed verbatim. This is the §0 receipt: the personal layer hands back the same
        # objective numbers it was given.
        core_axes=dict(view.axes),
        core_weakest_axis=weakest_axis,
        core_weakest_score=weakest_score,
        evidence_event_ids=[event.event_id for event in evidence],
        personal_recommendation=recommendation,
        recommendation_locked_reason=locked_reason,
        anti_memo=anti_memo if recommendation is not None else None,
    )


def _packet(view: CompanyView, evidence: list[Event], as_of: datetime) -> tuple[list[dict], set[str]]:
    """The narration packet. Same shape and same contents as the core council's, so the
    personas argue about the facts the memo can see and nothing else."""
    docs = [
        {
            "event_id": str(event.event_id),
            "kind": str(event.kind),
            "observed_at": event.observed_at.isoformat(),
            "text": council._event_text(event),
        }
        for event in evidence
    ]
    return (
        [{"events": docs, "axes": dict(view.axes), "as_of": as_of.isoformat()}],
        {doc["event_id"] for doc in docs},
    )


# ---------------------------------------------------------------------------
# Ranking — core beside personal, disagreement on top
# ---------------------------------------------------------------------------


def rank(
    views: list[CompanyView],
    core_order: list[UUID],
    profile: DerivedProfile,
    evidence_by_company: dict[UUID, list[Event]],
    as_of: datetime,
) -> PersonalRanking:
    """Re-rank an ALREADY-COMPUTED core order. The core order is an input, not an output.

    `core_order` arrives from the objective layer (`api.main.list_companies`, min-axis
    with a momentum tiebreak) and is never recomputed here — that is what makes it
    structurally impossible for a preference weight to have moved it. The personal layer
    only produces a second ordering and the differences between the two.

    Personalisation OFF is a first-class result: an empty `rows` with the reason stated,
    exactly as `memory/profiles.py` requires below the confidence threshold.
    """
    if not profile.personalisation_enabled:
        return PersonalRanking(
            as_of=as_of,
            personalised=False,
            reason=profile.personalisation_reason
            or "personalisation is off for this profile; the core ranking is unaffected",
        )

    lenses, not_derived = derive_lenses(profile)
    if len(lenses) < MIN_LENSES:
        return PersonalRanking(
            as_of=as_of,
            personalised=False,
            reason=(
                f"only {len(lenses)} council lens could be derived from this profile and "
                f"{MIN_LENSES} are required; a single lens is a renamed axis, not a council. "
                "The core objective ranking is unaffected and continues to work."
            ),
            lenses=lenses,
            lenses_not_derived=not_derived,
        )

    by_id = {view.company_id: view for view in views}
    core_rank = {cid: i for i, cid in enumerate(core_order, 1) if cid in by_id}

    fits: dict[UUID, PersonalFit] = {}
    for cid in core_rank:
        fits[cid] = score_company(
            by_id[cid],
            lenses,
            profile,
            evidence_by_company.get(cid, []),
            as_of,
        )

    # Ties broken by core rank, so the personal order only ever departs from core where
    # the lenses genuinely say something. A random tiebreak would manufacture divergence.
    ordered = sorted(core_rank, key=lambda cid: (-fits[cid].fit_score, core_rank[cid]))

    rows: list[PersonalRankRow] = []
    disagreements: list[Disagreement] = []
    agreements: list[str] = []
    for personal_position, cid in enumerate(ordered, 1):
        fit = fits[cid]
        view = by_id[cid]
        divergence = core_rank[cid] - personal_position
        scored = [item for item in fit.contributions if item.reading is not None]
        top = max(scored, key=lambda item: item.contribution) if scored else None
        rows.append(
            PersonalRankRow(
                company_id=cid,
                name=view.name,
                core_rank=core_rank[cid],
                personal_rank=personal_position,
                fit_score=fit.fit_score,
                core_weakest_score=fit.core_weakest_score,
                divergence=divergence,
                top_lens=top.lens if top else None,
                why=(
                    f"fit {fit.fit_score:.3f}; heaviest contribution "
                    f"{top.lens.value if top else 'none'} "
                    f"({top.contribution:.3f} = weight {top.weight:.3f} x reading "
                    f"{top.reading:.3f})"
                    if top
                    else f"fit {fit.fit_score:.3f}; every lens abstained on this company"
                ),
            )
        )
        if abs(divergence) >= DIVERGENCE_HEADLINE or fit.red_line_hits:
            disagreements.append(
                Disagreement(
                    company_id=cid,
                    name=view.name,
                    core_rank=core_rank[cid],
                    personal_rank=personal_position,
                    divergence=divergence,
                    explanation=_explain(divergence, fit, top),
                )
            )
        elif divergence == 0:
            agreements.append(
                f"{view.name}: core and personal both rank this {core_rank[cid]} — "
                "confirmation, not a finding"
            )

    return PersonalRanking(
        as_of=as_of,
        personalised=True,
        reason=profile.personalisation_reason,
        lenses=lenses,
        lenses_not_derived=not_derived,
        rows=rows,
        # The headline. §3: the disagreements are the value, agreement is just
        # confirmation — so they are sorted by how hard the two layers disagree.
        disagreements=sorted(disagreements, key=lambda item: (-abs(item.divergence), item.name)),
        agreements=agreements,
    )


def _explain(divergence: int, fit: PersonalFit, top: LensContribution | None) -> str:
    if fit.red_line_hits:
        lines = "; ".join(
            f"{hit.source} red line {hit.statement!r} (matched {hit.matched_on})"
            for hit in fit.red_line_hits
        )
        return (
            f"A red line fired on this company: {lines}. The core layer does not know "
            f"about your red lines and ranks it {fit.core_weakest_axis} "
            f"{fit.core_weakest_score:.3f} regardless — this divergence is entirely your "
            f"preference, not a fact about the company."
        )
    direction = "promotes" if divergence > 0 else "demotes"
    driver = (
        f"{top.lens.value} at weight {top.weight:.3f} reading {top.reading:.3f}"
        if top
        else "no lens produced a reading"
    )
    return (
        f"Your council {direction} this by {abs(divergence)} place(s) against core rank. "
        f"Driver: {driver}. Core ranks on its weakest axis "
        f"({fit.core_weakest_axis} {fit.core_weakest_score:.3f}) and is unchanged by this — "
        f"if you disagree with the move, the lens weight is the thing to argue with, not "
        f"the evidence."
    )


# ---------------------------------------------------------------------------
# Store-backed entry point
# ---------------------------------------------------------------------------


def view_from_screening(
    screening: ScreeningResult, *, name: str = "", sector: str | None = None, stage: str | None = None
) -> CompanyView:
    """A read-only copy of the core screen. The `ScreeningResult` itself never leaves
    the objective layer, which is how §0 stays enforced by construction rather than by
    discipline."""
    return CompanyView(
        company_id=screening.company_id,
        name=name,
        sector=sector,
        stage=stage,
        axes={name_: getattr(screening, name_).score for name_ in AXES},
        axis_confidence={name_: getattr(screening, name_).confidence for name_ in AXES},
        axis_evidence={name_: list(getattr(screening, name_).evidence_event_ids) for name_ in AXES},
    )


def personal_fit(
    company_id: UUID,
    as_of: datetime,
    profile: DerivedProfile,
    *,
    sector: str | None = None,
    stage: str | None = None,
    name: str = "",
    dissent_served: bool = False,
    judge: Judge = llm.complete,
) -> PersonalFit:
    """Store-backed single-company fit, with the council actually deliberating.

    The core council runs FIRST and on the same evidence, so the bear case behind any
    unlocked recommendation is the real one — the personal layer does not get to write
    its own dissent and mark its own homework.
    """
    from intelligence import screen
    from memory import store

    events = store.events(company_id=company_id, as_of=as_of)
    screening = screen.three_axis(company_id, as_of)
    evidence = usable_evidence(company_id, as_of, events)

    lenses, _ = derive_lenses(profile)
    result = council.deliberate_from_evidence(company_id, as_of, events, screening, judge=judge)
    return score_company(
        view_from_screening(screening, name=name, sector=sector, stage=stage),
        lenses,
        profile,
        evidence,
        as_of,
        judge=judge,
        anti_memo=result.anti_memo,
        dissent_served=dissent_served,
        core_decision=result.decision,
    )
