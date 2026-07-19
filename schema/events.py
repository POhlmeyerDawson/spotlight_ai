"""THE contract. Every module in this repo speaks Event.

Append-only: nothing is ever updated or deleted. Corrections are new events.
Owner: A. Changes require 4-person agreement (SHARED.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union
from uuid import UUID, uuid4

from ._backport import StrEnum

from pydantic import BaseModel, Field, field_validator


class EventKind(StrEnum):
    # sourcing (B)
    REPO_ACTIVITY = "repo_activity"
    COMMIT_BURST = "commit_burst"
    RELEASE = "release"
    PAPER = "paper"
    HN_POST = "hn_post"
    HN_COMMENT = "hn_comment"
    DECK_CLAIM = "deck_claim"
    PROFILE_FACT = "profile_fact"
    # intelligence (C)
    GREEN_FLAG = "green_flag"
    VALIDATION_RESULT = "validation_result"
    PROOF_CHALLENGE_ISSUED = "proof_challenge_issued"
    PROOF_ARTIFACT = "proof_artifact"
    PROOF_BEHAVIOR = "proof_behavior"
    CONTRADICTION = "contradiction"
    # cross-cutting
    INTEGRITY = "integrity"
    ENTITY_MERGE = "entity_merge"


class Source(StrEnum):
    GITHUB = "github"
    HN = "hn"
    ARXIV = "arxiv"
    WEB = "web"  # Tavily enrichment
    DECK = "deck"
    PROOF_PROTOCOL = "proof_protocol"
    VALIDATOR = "validator"
    MANUAL = "manual"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """One observation about the world, stamped with when the world produced it."""

    event_id: UUID = Field(default_factory=uuid4)
    entity_id: Optional[UUID] = None  # resolved person; None until entity resolution runs
    company_id: Optional[UUID] = None
    kind: EventKind
    source: Source
    source_url: Optional[str] = None

    observed_at: datetime  # WHEN THE WORLD PRODUCED IT — the only field scoring may filter on
    # When WE saw it. NEVER used in scoring, ranking, or any axis — "when we happened to
    # look" is not evidence about a founder, and a founder cannot act on it.
    #
    # Two uses are permitted and neither is scoring:
    #   1. a deterministic sort tiebreaker after observed_at (memory/store.py, pg_store).
    #   2. `sourcing/intake._events_recorded_before` — the arrival panel's "did we find
    #      them before they applied", which is a question ABOUT OUR OWN CLOCK and the
    #      only question this column can honestly answer. See that function's docstring
    #      for why observed_at would answer a different question and always say yes.
    # Anything else reading this field for a decision is a bug. Add to that list only
    # with an argument, not with a call site.
    ingested_at: datetime = Field(default_factory=utcnow)

    payload: dict = Field(default_factory=dict)
    evidence_span: Optional[str] = None  # exact quoted text / commit sha / slide id backing this
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)  # extraction confidence
    integrity_flags: list[str] = Field(default_factory=list)

    @field_validator("observed_at", "ingested_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        """Naive datetimes silently break as_of comparisons. Reject them at the boundary."""
        if v.tzinfo is None:
            raise ValueError("observed_at/ingested_at must be timezone-aware (use utcnow())")
        return v


# ---------------------------------------------------------------------------
# Entity resolution (A) — see A.md
# ---------------------------------------------------------------------------


class ResolutionStatus(StrEnum):
    MERGED = "merged"
    NEW = "new"
    AMBIGUOUS = "ambiguous"  # never guessed; surfaced in the memo


class EntityCandidate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    urls: list[str] = Field(default_factory=list)
    handles: dict[str, str] = Field(default_factory=dict)  # {"github": "x", "hn": "y"}
    source: Source


class Resolution(BaseModel):
    status: ResolutionStatus
    entity_id: UUID
    score: float
    alternatives: list[UUID] = Field(default_factory=list)  # populated when AMBIGUOUS
    rationale: str
    signals: list[str] = Field(default_factory=list)  # reason codes: which signals fired


class Entity(BaseModel):
    """A resolved person. The Founder Score belongs here, not to a company — it
    persists across applications, companies, and startup ideas."""

    entity_id: UUID = Field(default_factory=uuid4)
    display_name: str
    name_normalized: str  # unidecode + casefold — Type 6 fuzzy matching depends on it
    created_at: datetime = Field(default_factory=utcnow)


class CompanyProvenance(StrEnum):
    """Where a company's evidence came from. Not a quality judgement — a factual one.

    SOURCED means every event under this company was collected from the outside world:
    a scanner read it, an applicant submitted it, or it was reconstructed from public
    record that a reader can go and check.

    CONSTRUCTED means the evidence was AUTHORED for this repository — the archetype
    scenarios and the backtest's synthetic controls. Those exist for good reasons (a
    cohort of winners alone proves nothing, and a detector with no control is a claim
    rather than a test), but a constructed company must never be presentable as
    sourced evidence, which is what this field exists to make impossible.

    There is deliberately no third 'unknown' member. A company whose provenance nobody
    can state is one nobody should be reading evidence off, and a nullable field here
    would let that case pass silently through every consumer.
    """

    SOURCED = "sourced"
    CONSTRUCTED = "constructed"


class Company(BaseModel):
    company_id: UUID = Field(default_factory=uuid4)
    name: str
    founder_entity_ids: list[UUID] = Field(default_factory=list)
    archetype: int | None = None  # 1..6, seed data only
    # NO DEFAULT, ON PURPOSE. This used to default to SOURCED, reasoning that every
    # runtime writer is reading the real world. The reasoning was about the writers; the
    # field is read as a CLAIM about the evidence. Those come apart exactly when a write
    # half-succeeds, and a default that silently asserts "evidence-backed" is only ever
    # wrong in the direction that overclaims.
    #
    # It is not inverted to CONSTRUCTED either, which would be a different false claim —
    # 'constructed' means "authored for this repository", and stamping it on a real
    # scraped company would poison the fame-vs-fitness gate that reads this column. When
    # we do not know, neither value is honest, so there is nothing safe to default TO and
    # the write has to say. Both store backends already pass it explicitly.
    #
    # Measured cost of the old default: 8 companies in the live store carry
    # provenance='sourced' with zero events under them — real Show HN companies from a
    # discover run whose evidence fan-out returned nothing. The column asserted
    # evidence-backing for all 8; nothing downstream could tell.
    provenance: CompanyProvenance
    created_at: datetime = Field(default_factory=utcnow)


class Observation(BaseModel):
    """The typed input to the Founder Score filter. A owns this boundary; C produces
    the underlying GREEN_FLAG / PROOF_* events, A maps them to observations here.

    Never reach into C's code for these — read the events C wrote and map at the
    boundary (see memory/score.build_observations)."""

    entity_id: UUID
    observed_at: datetime  # as_of filtering happens on this
    value: float = Field(ge=0.0, le=1.0)  # y_t — weighted YES-rate / capability proxy
    self_consistency: float = Field(default=1.0, gt=0.0, le=1.0)  # agreement of the read
    source_penalty: float = Field(default=1.0, ge=0.0)  # >1 noisier, <1 low-noise (proof events)
    event_ids: list[UUID] = Field(default_factory=list)  # receipts — flow to the score
    rule_ids: list[str] = Field(default_factory=list)  # which green-flag rules fired


class FounderScore(BaseModel):
    """Output of the local-linear-trend filter. mu/band/trend, always with receipts."""

    entity_id: UUID
    as_of: datetime
    mu: float  # capability level (posterior mean)
    band: float  # sqrt(P[0,0]) — displayed, never hidden
    trend: float  # nu — momentum, structural, not a diff of scores
    contributing_event_ids: list[UUID] = Field(default_factory=list)
    model: str = "kalman"  # or "beta_binomial" when the fallback flag is on


# ---------------------------------------------------------------------------
# Screening / validation / decisions (C) — see C.md
# ---------------------------------------------------------------------------


class Axis(BaseModel):
    """One screening axis.

    `score` and `trend` are OPTIONAL, and that is the whole point. An axis we could
    not score — no events, a judge that failed, a malformed reply, no citable
    receipts — must return None, not a middling number. The previous fallback
    returned 0.5 in all four of those cases, which fed `rank_key` and let "we could
    not look" compete against real readings as though it were a measurement. A
    confident 0.5 on no evidence is the strongest claim the system can make on the
    weakest grounds.

    `reason` carries WHY it is None so the client can say which of the four it was.

    `confidence` is OPTIONAL for the same reason `score` is. A 0.0 here used to be the
    unscorable case's stand-in, which made "the judge never ran" arithmetically
    identical to "the judge ran and trusts its answer not at all". Downstream that
    difference is the whole ballgame: `custom_council._evidence_bar_reading` averages
    these into an evidence-sufficiency term, and a fabricated 0.0 drags the mean down
    exactly as a measured no-confidence would. None means NOT MEASURED and every
    consumer must skip it rather than average it in.
    """

    score: float | None = None
    trend: float | None = None
    confidence: float | None = None
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    reason: str | None = None


class ScreeningResult(BaseModel):
    """Three axes. NEVER averaged into one number — not here, not in the UI."""

    company_id: UUID
    as_of: datetime
    founder: Axis
    market: Axis
    idea_vs_market: Axis


class ClaimStatus(StrEnum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"  # we looked, nothing exists to check against
    NOT_ATTEMPTED = "not_attempted"  # we didn't look — say so


class ClaimVerdict(BaseModel):
    claim_id: UUID = Field(default_factory=uuid4)
    company_id: UUID
    claim_text: str
    claim_source_span: str  # e.g. "slide 7" — where the founder said it
    status: ClaimStatus
    trust: float = Field(ge=0.0, le=1.0)  # per-claim. There is no company-level trust number.
    corroborating_url: Optional[str] = None
    corroborating_span: Optional[str] = None  # a VERIFIED with no span is NOT_ATTEMPTED
    self_published: bool = False  # weight below independent sources
    claim_asserted_at: Optional[datetime] = None  # timestamps decide fraud-shaped vs time-shaped
    counter_evidence_at: Optional[datetime] = None


class GateOutcome(StrEnum):
    PROCEED = "proceed"
    PROOF_PROTOCOL = "proof_protocol"  # thin evidence — create some
    NO_CALL = "no_call"


class GateDecision(BaseModel):
    company_id: UUID
    outcome: GateOutcome
    rationale: str
    absence_is_suspicious: bool = False  # vs absence-because-irrelevant. See C.md.


class Challenge(BaseModel):
    challenge_id: UUID = Field(default_factory=uuid4)
    company_id: UUID
    prompt: str
    central_claim: str  # what from the deck this is testing
    ambiguous_requirement: str  # do they ask, or assume-and-state?
    planted_bad_constraint: str  # do they push back, or comply?
    issued_at: datetime = Field(default_factory=utcnow)


class AntiMemo(BaseModel):
    company_id: UUID
    bear_case: str
    weakest_evidence: list[str]
    load_bearing_claim: str  # the single claim that kills the thesis if false. Named, not hedged.
    axis_spreads: dict[str, float] = Field(default_factory=dict)  # bull/bear gap -> uncertainty


# ---------------------------------------------------------------------------
# Sourcing (B) — see B.md
# ---------------------------------------------------------------------------


class RawSignal(BaseModel):
    source: Source
    source_url: Optional[str] = None
    content: Union[str, bytes]
    fetched_at: datetime = Field(default_factory=utcnow)
    meta: dict = Field(default_factory=dict)


class HiddenCandidate(BaseModel):
    """High proximity to greatness, low individual visibility. The pre-signal founder."""

    entity_id: UUID
    ppr: float
    visibility: float
    hidden_score: float  # z(ppr) - z(visibility)
