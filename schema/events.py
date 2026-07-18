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
    ingested_at: datetime = Field(default_factory=utcnow)  # when we saw it. NEVER used in scoring.

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
    score: float
    trend: float
    confidence: float
    evidence_event_ids: list[UUID] = Field(default_factory=list)


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
