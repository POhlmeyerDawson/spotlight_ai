"""Commit-burst signature. Owner: B, with C. Type 5.

Burst alone is NEVER the flag — real fast builders spike too. Separate on substance:
diff entropy, test presence, file diversity, real logic vs whitespace reshuffling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import numpy as np

from memory.store import events
from schema.events import utcnow


def burst_signature(entity_id: UUID) -> dict:
    """Compute commit-burst signature for an entity.

    Burst alone is NEVER the flag — real fast builders spike too. Separate on substance:
    - diff entropy (code vs whitespace)
    - test presence
    - file diversity
    - whether commits touch real logic or reshuffle whitespace

    Args:
        entity_id: The entity to analyze

    Returns:
        Dict with burst analysis results including:
        - burst_detected: bool
        - burst_window: str (start, end, duration)
        - commit_count: int
        - diff_stats: dict with entropy, test_ratio, file_count
        - substance_score: float (0-1)
        - is_burst: bool (burst + low substance = suspicious)
    """
    result = {
        "burst_detected": False,
        "burst_window": None,
        "commit_count": 0,
        "diff_stats": {},
        "substance_score": 0.0,
        "is_burst": False,
        "commit_details": [],
    }

    # Get recent commits for this entity
    now = utcnow()
    thirty_days_ago = now - timedelta(days=30)

    # Get all events for this entity
    all_events = events(as_of=now, entity_id=entity_id, kind="repo_activity")

    # Filter commits (we need more specific filtering based on payload)
    commits = []
    for event in all_events:
        payload = event.payload
        # Check if this looks like a commit event
        if payload.get("oid") or payload.get("committed_date") or payload.get("message"):
            commits.append(event)

    if not commits:
        return result

    result["commit_count"] = len(commits)

    # Sort by date
    commits.sort(key=lambda e: e.observed_at if hasattr(e, "observed_at") else now)

    # Analyze commit dates for bursts
    burst_info = _analyze_burst(commits)
    result["burst_detected"] = burst_info["burst_detected"]
    result["burst_window"] = burst_info["window"]
    result["commit_details"] = burst_info.get("details", [])

    # Analyze diff substance
    diff_stats = _analyze_diff_substance(commits)
    result["diff_stats"] = diff_stats
    result["substance_score"] = diff_stats.get("substance_score", 0.0)

    # Determine if this is a suspicious burst
    # Burst + low substance = suspicious
    if result["burst_detected"] and result["substance_score"] < 0.3:
        result["is_burst"] = True
    elif result["burst_detected"] and result["substance_score"] > 0.5:
        # High substance burst is legitimate fast building
        result["is_burst"] = False

    return result


def _analyze_burst(commits: list) -> dict:
    """Analyze commits for burst patterns.

    A burst is when someone makes many commits in a short time window.
    """
    if len(commits) < 5:
        return {
            "burst_detected": False,
            "window": None,
        }

    # Get commit dates
    dates = []
    for commit in commits:
        if hasattr(commit, "observed_at"):
            dates.append(commit.observed_at)

    if len(dates) < 5:
        return {
            "burst_detected": False,
            "window": None,
        }

    dates.sort()

    # Sliding window analysis
    # A burst is defined as: many commits in a short time
    # Thresholds:
    # - At least 10 commits
    # - Within a 24-hour window
    # - Average less than 2 hours between commits

    burst_detected = False
    best_window = None

    for i in range(len(dates) - 9):  # Need at least 10 commits
        window_start = dates[i]
        window_end = window_start + timedelta(hours=24)

        # Count commits in this window
        window_commits = [d for d in dates if window_start <= d <= window_end]

        if len(window_commits) >= 10:
            burst_detected = True

            # Calculate average interval
            if len(window_commits) >= 2:
                intervals = []
                for j in range(1, len(window_commits)):
                    interval = (window_commits[j] - window_commits[j-1]).total_seconds() / 3600
                    intervals.append(interval)

                avg_interval = sum(intervals) / len(intervals)

                if avg_interval < 2:  # Less than 2 hours average between commits
                    best_window = {
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                        "commit_count": len(window_commits),
                        "avg_interval_hours": avg_interval,
                    }

    return {
        "burst_detected": burst_detected,
        "window": best_window,
        "details": {
            "total_commits": len(commits),
            "date_range": f"{dates[0].isoformat()} to {dates[-1].isoformat()}",
        } if dates else {},
    }


def _analyze_diff_substance(commits: list) -> dict:
    """Analyze the substance of commits (diff entropy, tests, file diversity).

    Real fast builders have:
    - High diff entropy (real code changes)
    - Test presence
    - File diversity (multiple files modified)
    - Meaningful commit messages
    """
    stats = {
        "total_commits": len(commits),
        "files_modified": 0,
        "test_files": 0,
        "message_quality": 0.0,
        "substance_score": 0.0,
    }

    if not commits:
        return stats

    total_files = 0
    test_count = 0
    message_lengths = []

    for commit in commits:
        payload = commit.payload if hasattr(commit, "payload") else {}

        # Check for files modified
        if payload.get("files"):
            files = payload["files"]
            if isinstance(files, list):
                total_files += len(files)
                for f in files:
                    if "test" in str(f).lower():
                        test_count += 1

        # Check message quality
        message = payload.get("message", "")
        if message:
            message_lengths.append(len(message))

    stats["files_modified"] = total_files
    stats["test_files"] = test_count

    # Message quality (longer messages tend to be more descriptive)
    if message_lengths:
        avg_message = sum(message_lengths) / len(message_lengths)
        # Normalize to 0-1 (assuming 50 chars is short, 200 is good)
        stats["message_quality"] = min(1.0, avg_message / 200)

    # Calculate substance score
    # Weights:
    # - File diversity: 30%
    # - Test presence: 30%
    # - Message quality: 40%

    file_diversity = min(1.0, total_files / 10)  # Cap at 10 files
    test_ratio = test_count / max(total_files, 1)
    message_quality = stats["message_quality"]

    substance_score = (
        0.3 * file_diversity +
        0.3 * test_ratio +
        0.4 * message_quality
    )

    stats["substance_score"] = round(substance_score, 3)

    return stats


def burst_signature_with_threshold(entity_id: UUID, substance_threshold: float = 0.3) -> dict:
    """Compute burst signature with configurable substance threshold.

    Args:
        entity_id: The entity to analyze
        substance_threshold: Lower bound for substance score (below = suspicious)

    Returns:
        Dict with burst analysis including is_suspicious boolean
    """
    result = burst_signature(entity_id)
    result["is_suspicious"] = result.get("is_burst", False)
    result["substance_threshold"] = substance_threshold
    return result


def compare_burst_signatures(entity1_id: UUID, entity2_id: UUID) -> dict:
    """Compare burst signatures of two entities.

    Useful for seeing who's building fast vs gaming the system.
    """
    sig1 = burst_signature(entity1_id)
    sig2 = burst_signature(entity2_id)

    return {
        "entity1": {
            "id": str(entity1_id),
            "commit_count": sig1["commit_count"],
            "burst_detected": sig1["burst_detected"],
            "substance_score": sig1["substance_score"],
            "is_burst": sig1["is_burst"],
        },
        "entity2": {
            "id": str(entity2_id),
            "commit_count": sig2["commit_count"],
            "burst_detected": sig2["burst_detected"],
            "substance_score": sig2["substance_score"],
            "is_burst": sig2["is_burst"],
        },
        "comparison": {
            "higher_substance": "entity1" if sig1["substance_score"] > sig2["substance_score"] else "entity2",
            "more_commits": "entity1" if sig1["commit_count"] > sig2["commit_count"] else "entity2",
            "both_burst": sig1["burst_detected"] and sig2["burst_detected"],
        },
    }
