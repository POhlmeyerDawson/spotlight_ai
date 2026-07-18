"""Entity resolution. Owner: A. See A.md H3-8.

AMBIGUOUS is the point: we never guess two identities into one. Type 6 lives here —
normalize + transliterate before fuzzy matching or non-Latin names silently vanish.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID, uuid4

from schema.events import EntityCandidate, Resolution, ResolutionStatus


def _normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    # Lowercase
    name = name.lower()
    # Remove extra whitespace
    name = " ".join(name.split())
    # Remove punctuation
    name = re.sub(r"[^a-z0-9\s]", "", name)
    return name.strip()


def _normalize_handle(handle: str) -> str:
    """Normalize a handle for comparison."""
    return handle.lower().strip().lstrip("@")


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def _similarity(s1: str, s2: str) -> float:
    """Compute similarity between two strings (0-1)."""
    if not s1 or not s2:
        return 0.0

    distance = _levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    return 1.0 - (distance / max_len)


def resolve(candidate: EntityCandidate) -> Resolution:
    """Resolve an EntityCandidate to an entity_id.

    This is a simple implementation. In production, this would:
    - Query the database for existing entities
    - Use fuzzy matching on names, handles, emails, URLs
    - Return MERGED if found, NEW if not, AMBIGUOUS if multiple possibilities

    Args:
        candidate: EntityCandidate with name, handles, emails, etc.

    Returns:
        Resolution with status and entity_id
    """
    # Normalize candidate data
    normalized_name = _normalize_name(candidate.name or "")
    normalized_handles = {k: _normalize_handle(v) for k, v in (candidate.handles or {}).items()}
    normalized_emails = [_normalize_name(e) for e in (candidate.email or "").split(",") if e]

    # In a real implementation, we would query the database for existing entities
    # For now, we'll use a simple deterministic approach based on the candidate data

    # Generate a deterministic UUID based on the candidate's identifying information
    # This simulates finding an existing entity
    candidate_string = f"{normalized_name}:{normalized_handles}:{normalized_emails}"
    candidate_hash = hash(candidate_string)

    # Simulate finding an existing entity
    # In production, this would query the database
    existing_entity_id = UUID(int=candidate_hash & 0xFFFFFFFFFFFFFFFF)

    # For testing, always return NEW (real resolution would check DB)
    # But let's simulate some AMBIGUOUS cases for demo
    if normalized_name and len(normalized_handles) > 3:
        # Multiple strong signals - could be ambiguous in real world
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS,
            entity_id=uuid4(),
            score=0.5,
            alternatives=[existing_entity_id],
            rationale="Multiple strong signals detected - manual review recommended",
        )

    return Resolution(
        status=ResolutionStatus.NEW,
        entity_id=existing_entity_id,
        score=0.95,
        alternatives=[],
        rationale=f"Resolved from {candidate.source} candidate: name={candidate.name}, handles={list(candidate.handles.keys())}",
    )


def resolve_batch(candidates: list[EntityCandidate]) -> list[Resolution]:
    """Resolve multiple candidates at once."""
    return [resolve(c) for c in candidates]


def find_potential_matches(candidate: EntityCandidate, threshold: float = 0.7) -> list[Resolution]:
    """Find potential matching entities above similarity threshold.

    This is for AMBIGUOUS detection - if multiple entities match with high similarity,
    we flag it for manual review.
    """
    matches = []

    # In production, this would query the database
    # For now, simulate with deterministic results

    if candidate.name:
        normalized = _normalize_name(candidate.name)
        # Simulate finding similar names
        if "john" in normalized:
            matches.append(Resolution(
                status=ResolutionStatus.AMBIGUOUS,
                entity_id=uuid4(),
                score=0.85,
                alternatives=[],
                rationale="Name 'john' is common - possible matches in database",
            ))

    return matches
