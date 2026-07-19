"""Trait taxonomy + per-source attribution. Mapping only — no scoring pipeline here.

Scoring is on TRAITS; attribution is by SOURCE. See docs/TRAITS.md for the argument
and data/traits.json for the machine-readable taxonomy.

Two properties this module is built to guarantee:

  1. It changes no score. The collapse to the scalar that memory/score.py consumes is
     an algebraic IDENTITY with intelligence/flags.observation()[0], because a trait's
     collapse weight is exactly the weight mass of the applicable flags.py rules mapped
     to it. Reorganising the explanation must not move a band.
  2. Per-source attribution is COMPUTED, not asserted. A source's contribution to a
     trait is the leave-one-source-out marginal: re-run the rules with that source's
     events removed and take the difference. That makes "GitHub contributed +8 to
     iteration-velocity" a measurement rather than a caption.

This module reads intelligence/flags.py and never modifies it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from intelligence import flags
from schema.events import Event, EventKind

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "data" / "traits.json"


@lru_cache(maxsize=1)
def taxonomy() -> dict:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def rule_to_trait() -> dict[str, str]:
    return {
        rule_id: trait["id"]
        for trait in taxonomy()["traits"]
        for rule_id in trait["flag_rules"]
    }


@lru_cache(maxsize=1)
def unmapped_rule_ids() -> tuple[str, ...]:
    """Rules that deliberately map to no trait. See data/traits.json unmapped_rules."""
    return tuple(entry["rule_id"] for entry in taxonomy()["unmapped_rules"])


@lru_cache(maxsize=1)
def trait_ids() -> tuple[str, ...]:
    return tuple(trait["id"] for trait in taxonomy()["traits"])


def _trait_def(trait_id: str) -> dict:
    return next(t for t in taxonomy()["traits"] if t["id"] == trait_id)


# --- corroboration ----------------------------------------------------------------
# Confidence only. Never multiplied into the trait score, so it cannot reach the
# scalar and cannot destabilise the filter's band.
_CORROBORATION = {0: 0.0, 1: 0.6, 2: 0.8}
_CORROBORATION_CEIL = 1.0


def corroboration(n_channels: int) -> float:
    return _CORROBORATION.get(max(n_channels, 0), _CORROBORATION_CEIL)


@lru_cache(maxsize=1)
def self_attested_channels() -> frozenset[str]:
    """Sources that can be the first voice but never the second.

    A deck is founder-authored with no third party in the loop; a MANUAL event is us.
    Counting either as corroboration counts one person twice — which is exactly how
    the Type 5 adversary cleared the two-channel gate on problem_scoping before this
    existed. See data/traits.json corroboration.self_attested_rationale.
    """
    return frozenset(taxonomy()["corroboration"].get("self_attested_channels", ()))


def independent_channels(channels: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(channels) - self_attested_channels()))


@dataclass(frozen=True)
class TraitScore:
    trait_id: str
    score: float  # weighted YES-rate over APPLICABLE rules mapped to this trait
    weight_mass: float  # sum of applicable rule weights -> the collapse weight
    applicable_rules: tuple[str, ...]
    fired_rules: tuple[str, ...]
    channels: tuple[str, ...]  # DISTINCT sources backing the fired rules
    corroboration: float
    confidence: float  # extraction confidence * corroboration
    observed: bool  # channels >= the trait's min_channels
    min_channels: int
    absence: str  # MEANINGFUL | UNKNOWN | CONDITIONAL
    absence_predicate: str

    @property
    def evidenced(self) -> bool:
        return bool(self.fired_rules)


@dataclass(frozen=True)
class SourceContribution:
    """Marginal contribution of ONE source to ONE trait, in trait points (0..100)."""

    source: str
    trait_id: str
    delta_points: float
    rules_fired: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    sole_channel: bool = False
    """True when removing this source leaves the trait with NO applicable rules.

    The delta is then not a point movement against a comparable baseline — it is the
    whole trait. The UI must render these as "GitHub is the only reason this trait
    could be assessed at all", never as a numeric bar next to genuine marginals, or a
    founder visible on one platform will appear to have an enormous source effect
    that is really just single-channel fragility.
    """


@dataclass
class TraitProfile:
    entity_id: UUID
    as_of: datetime
    traits: dict[str, TraitScore]
    scalar: float
    attribution: list[SourceContribution] = field(default_factory=list)
    unmapped_fired: tuple[str, ...] = ()
    sources_present: tuple[str, ...] = ()

    def vector(self) -> dict[str, float | None]:
        """Trait -> score, or None where the trait was not applicable at all."""
        return {
            trait_id: (
                self.traits[trait_id].score if self.traits[trait_id].applicable_rules else None
            )
            for trait_id in trait_ids()
        }

    def by_source(self) -> dict[str, list[SourceContribution]]:
        out: dict[str, list[SourceContribution]] = {}
        for contribution in self.attribution:
            out.setdefault(contribution.source, []).append(contribution)
        return out


# --- core -------------------------------------------------------------------------


def _scoped(events: Sequence[Event], entity_id: UUID, as_of: datetime) -> list[Event]:
    """Same scoping flags.evaluate_events applies, so the two see one corpus.

    Uses flags.is_impeached rather than testing integrity_flags for emptiness: a
    transliterated name is a provenance note, not grounds to void the evidence.
    """
    return [
        e
        for e in events
        if e.entity_id == entity_id
        and e.observed_at <= as_of
        and e.kind not in {EventKind.GREEN_FLAG, EventKind.INTEGRITY}
        and not flags.is_impeached(e)
    ]


def _rule_rows(events: Sequence[Event], entity_id: UUID, as_of: datetime) -> list[dict]:
    """(rule_id, weight, fired, evidence sources) per APPLICABLE rule."""
    by_id = {e.event_id: e for e in events}
    rows = []
    for flag_event in flags.evaluate_events(list(events), entity_id=entity_id, as_of=as_of):
        payload = flag_event.payload
        evidence_ids = [str(x) for x in payload.get("evidence_event_ids", [])]
        sources = []
        for raw in evidence_ids:
            try:
                event = by_id.get(UUID(raw))
            except ValueError:
                event = None
            if event is not None:
                sources.append(str(event.source))
        rows.append(
            {
                "rule_id": payload["rule_id"],
                "weight": float(payload.get("weight", 0.0)),
                "fired": bool(payload.get("fired")),
                "confidence": flag_event.confidence,
                "sources": sources,
                "evidence_event_ids": evidence_ids,
            }
        )
    return rows


def _traits_from_rows(rows: Sequence[dict]) -> dict[str, TraitScore]:
    mapping = rule_to_trait()
    buckets: dict[str, list[dict]] = {trait_id: [] for trait_id in trait_ids()}
    for row in rows:
        trait_id = mapping.get(row["rule_id"])
        if trait_id is not None:
            buckets[trait_id].append(row)

    out: dict[str, TraitScore] = {}
    for trait_id, bucket in buckets.items():
        definition = _trait_def(trait_id)
        mass = sum(row["weight"] for row in bucket)
        fired = [row for row in bucket if row["fired"]]
        fired_mass = sum(row["weight"] for row in fired)
        channels = tuple(sorted({source for row in fired for source in row["sources"]}))
        corr = corroboration(len(independent_channels(channels)))
        mean_conf = sum(row["confidence"] for row in bucket) / len(bucket) if bucket else 1.0
        min_channels = int(definition.get("min_channels", 1))
        out[trait_id] = TraitScore(
            trait_id=trait_id,
            score=(fired_mass / mass) if mass > 0 else 0.0,
            weight_mass=mass,
            applicable_rules=tuple(row["rule_id"] for row in bucket),
            fired_rules=tuple(row["rule_id"] for row in fired),
            channels=channels,
            corroboration=corr,
            confidence=mean_conf * corr,
            observed=len(independent_channels(channels)) >= min_channels,
            min_channels=min_channels,
            absence=definition["absence"],
            absence_predicate=definition.get("absence_predicate", ""),
        )
    return out


def collapse(traits: dict[str, TraitScore]) -> float:
    """Trait vector -> the ONE scalar memory/score.py consumes.

    Weight = applicable rule-weight mass, which makes this algebraically identical to
    flags.observation()[0]. That identity is the point: it lets the taxonomy ship
    without re-tuning the sensor or moving anybody's band. See data/traits.json
    "collapse" for why any other weighting is a re-tuning in disguise, and what this
    collapse loses (all per-trait uncertainty).
    """
    total = sum(trait.weight_mass for trait in traits.values())
    if total <= 0:
        return flags.UNINFORMATIVE[0]
    return sum(trait.weight_mass * trait.score for trait in traits.values()) / total


def profile(
    entity_id: UUID,
    as_of: datetime,
    events: Sequence[Event] | None = None,
    *,
    attribute: bool = True,
) -> TraitProfile:
    """Trait profile for one founder, as_of-scoped. Reads the store if events is None."""
    if events is None:
        from memory import store

        events = store.events(entity_id=entity_id, as_of=as_of)
    scoped = _scoped(events, entity_id, as_of)

    rows = _rule_rows(scoped, entity_id, as_of)
    traits = _traits_from_rows(rows)
    unmapped = tuple(
        row["rule_id"] for row in rows if row["fired"] and row["rule_id"] in unmapped_rule_ids()
    )
    sources_present = tuple(sorted({str(e.source) for e in scoped}))

    attribution: list[SourceContribution] = []
    if attribute:
        attribution = _attribute(scoped, entity_id, as_of, traits, rows)

    return TraitProfile(
        entity_id=entity_id,
        as_of=as_of,
        traits=traits,
        scalar=collapse(traits),
        attribution=attribution,
        unmapped_fired=unmapped,
        sources_present=sources_present,
    )


def _attribute(
    scoped: Sequence[Event],
    entity_id: UUID,
    as_of: datetime,
    traits: dict[str, TraitScore],
    rows: Sequence[dict],
) -> list[SourceContribution]:
    """Leave-one-source-out marginal per (source, trait), in trait points.

    Removing a source also removes any rule it gated (flags.py skips rules whose
    required source is absent), so the denominator moves too. That is deliberate and
    is what "marginal contribution of this source's evidence" honestly means: the
    counterfactual is a world where we never looked at that source, not a world where
    we looked and found nothing. The two differ, and SOURCES.md §4 already insists
    they be displayed differently (SEARCHED_EMPTY vs NOT_ATTEMPTED).
    """
    mapping = rule_to_trait()
    present = sorted({str(e.source) for e in scoped})
    out: list[SourceContribution] = []
    for source in present:
        remainder = [e for e in scoped if str(e.source) != source]
        without = (
            _traits_from_rows(_rule_rows(remainder, entity_id, as_of))
            if remainder
            else {trait_id: None for trait_id in trait_ids()}
        )
        for trait_id, trait in traits.items():
            baseline = without.get(trait_id)
            sole = baseline is None or baseline.weight_mass <= 0
            delta = trait.score - (0.0 if sole else baseline.score)
            fired_here = tuple(
                row["rule_id"]
                for row in rows
                if row["fired"]
                and mapping.get(row["rule_id"]) == trait_id
                and source in row["sources"]
            )
            if abs(delta) < 1e-9 and not fired_here and not sole:
                continue
            if sole and not trait.applicable_rules:
                continue
            evidence = tuple(
                sorted(
                    {
                        event_id
                        for row in rows
                        if row["fired"]
                        and mapping.get(row["rule_id"]) == trait_id
                        and source in row["sources"]
                        for event_id in row["evidence_event_ids"]
                    }
                )
            )
            out.append(
                SourceContribution(
                    source=source,
                    trait_id=trait_id,
                    delta_points=round(delta * 100.0, 1),
                    rules_fired=fired_here,
                    evidence_event_ids=evidence,
                    sole_channel=sole,
                )
            )
    return out
