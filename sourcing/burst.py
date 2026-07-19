"""Commit-burst signature. Owner: B, with C. Type 5.

Burst alone is NEVER the flag — real fast builders spike too. Separate on substance:
diff entropy, test presence, file diversity, real logic vs whitespace reshuffling.

`suspicious` therefore requires burst AND low substance. A high-volume committer with
real diffs across real files reads clean, which is the entire point: a false positive on
a legitimate hacker is the failure mode that gets called out in Q&A.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from uuid import UUID

from memory import store
from schema.events import EventKind, utcnow

log = logging.getLogger(__name__)

# Burst: a peak day this many times the entity's own daily average, and at least this
# many commits in that day. Relative to self, so a naturally prolific committer does not
# read as bursty merely for being prolific.
BURST_RATIO_MIN = 3.0
BURST_MIN_PEAK_COMMITS = 10

MIN_REAL_DIFF_LINES = 3  # below this a diff is cosmetic: a rename, a reformat
SUBSTANCE_FLOOR = 0.35  # burst AND substance under this = suspicious
TEST_RATIO_TARGET = 0.15  # test presence saturates here; nobody ships 100% test commits

_BURST_KINDS = (EventKind.REPO_ACTIVITY, EventKind.COMMIT_BURST, EventKind.RELEASE)
_TEST_MARKERS = ("test", "spec", "__tests__")
_TRIVIAL_MSG = ("whitespace", "reformat", "format", "lint", "reorder", "reshuffle", "typo")


def _commits(payload: dict, observed_at: datetime) -> list[dict]:
    """An event may carry a commit list, or be a single commit itself."""
    raw = payload.get("commits")
    items = raw if isinstance(raw, list) else [payload]
    out: list[dict] = []
    for c in items:
        if not isinstance(c, dict):
            continue
        files = [str(f) for f in (c.get("files") or c.get("changed_files") or []) if f]
        adds, dels = c.get("additions", c.get("added", 0)), c.get("deletions", c.get("removed", 0))
        at = c.get("authored_at") or c.get("committed_at")
        out.append(
            {
                "files": files,
                "lines": float(adds if isinstance(adds, (int, float)) else 0)
                + float(dels if isinstance(dels, (int, float)) else 0),
                "message": str(c.get("message") or ""),
                "whitespace_only": bool(c.get("whitespace_only")),
                "at": datetime.fromisoformat(at) if isinstance(at, str) else observed_at,
            }
        )
    return out


def _is_trivial(c: dict) -> bool:
    if c["whitespace_only"] or c["lines"] < MIN_REAL_DIFF_LINES:
        return True
    msg = c["message"].lower()
    return any(m in msg for m in _TRIVIAL_MSG) and c["lines"] < MIN_REAL_DIFF_LINES * 5


def _entropy(weights: list[float]) -> float:
    """Normalized Shannon entropy of changed lines across files. 1.0 = work spread evenly;
    near 0 = the same file hammered repeatedly, which is what padding looks like."""
    total = sum(weights)
    if total <= 0 or len(weights) < 2:
        return 0.0
    h = -sum((w / total) * math.log(w / total) for w in weights if w > 0)
    return h / math.log(len(weights))


def _empty(entity_id: UUID, as_of: datetime) -> dict:
    return {
        "entity_id": entity_id,
        "as_of": as_of,
        "commits": 0,
        "peak_day_commits": 0,
        "burst_ratio": 0.0,
        "burst": False,
        "diff_entropy": 0.0,
        "test_ratio": 0.0,
        "file_diversity": 0.0,
        "logic_ratio": 0.0,
        "substance": 0.0,
        "suspicious": False,
        "rationale": "no commit evidence",
    }


def burst_signature(entity_id: UUID, *, as_of: datetime | None = None) -> dict:
    as_of = as_of or utcnow()
    commits: list[dict] = []
    for ev in store.events(as_of=as_of, entity_id=entity_id):
        if ev.kind in _BURST_KINDS:
            commits += _commits(ev.payload if isinstance(ev.payload, dict) else {}, ev.observed_at)
    if not commits:
        return _empty(entity_id, as_of)

    # --- burst -------------------------------------------------------------
    per_day: Counter[datetime] = Counter()
    for c in commits:
        per_day[c["at"].replace(hour=0, minute=0, second=0, microsecond=0)] += 1
    peak = max(per_day.values())
    span_days = max((max(per_day) - min(per_day)) / timedelta(days=1) + 1, 1.0)
    burst_ratio = peak / (len(commits) / span_days)
    burst = burst_ratio >= BURST_RATIO_MIN and peak >= BURST_MIN_PEAK_COMMITS

    # --- substance ---------------------------------------------------------
    lines_per_file: dict[str, float] = defaultdict(float)
    touches = 0
    for c in commits:
        for f in c["files"]:
            lines_per_file[f] += c["lines"] / max(len(c["files"]), 1)
            touches += 1

    diff_entropy = _entropy(list(lines_per_file.values()))
    file_diversity = len(lines_per_file) / touches if touches else 0.0
    test_ratio = sum(
        1 for c in commits if any(m in f.lower() for f in c["files"] for m in _TEST_MARKERS)
    ) / len(commits)
    logic_ratio = sum(1 for c in commits if not _is_trivial(c)) / len(commits)
    substance = (
        diff_entropy + min(test_ratio / TEST_RATIO_TARGET, 1.0) + file_diversity + logic_ratio
    ) / 4.0
    suspicious = burst and substance < SUBSTANCE_FLOOR

    if suspicious:
        rationale = (
            f"burst x{burst_ratio:.1f} (peak {peak}/day) with substance {substance:.2f} < "
            f"{SUBSTANCE_FLOOR}: entropy {diff_entropy:.2f}, tests {test_ratio:.2f}, "
            f"diversity {file_diversity:.2f}, real-logic {logic_ratio:.2f}"
        )
    elif burst:
        rationale = f"burst x{burst_ratio:.1f} but substance {substance:.2f} holds up — real work"
    else:
        rationale = f"no burst (x{burst_ratio:.1f}, peak {peak}/day)"
    log.debug("burst: %s %s", entity_id, rationale)

    return {
        "entity_id": entity_id,
        "as_of": as_of,
        "commits": len(commits),
        "peak_day_commits": peak,
        "burst_ratio": burst_ratio,
        "burst": burst,
        "diff_entropy": diff_entropy,
        "test_ratio": test_ratio,
        "file_diversity": file_diversity,
        "logic_ratio": logic_ratio,
        "substance": substance,
        "suspicious": suspicious,
        "rationale": rationale,
    }
