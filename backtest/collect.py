"""Early-footprint collection for the time machine. Owner: D. See D.md H1-3.

Collect a founder's pre-breakout signals and TRUNCATE them at an explicit date. The
truncation date is recorded on every record — it is the thing that makes the replay a
backtest rather than a retelling, so it is never implicit and never defaulted.

Live scanners when B's are importable; otherwise the hand-collected fixture cohort in
data/seed/backtest.json. Collection is manual and slow; that is why it starts at H1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schema.events import Event

log = logging.getLogger(__name__)

SEED_PATH = Path("data/seed/backtest.json")
SCANNERS = ("github", "hn", "arxiv")


@dataclass
class Footprint:
    """What we knew about a founder as of the truncation date. Nothing after it."""

    founder: str
    truncation_date: datetime  # explicit, always. No default anywhere in this file.
    company_id: str | None = None
    label: str = "unknown"  # winner | control | failure
    events: list[Event] = field(default_factory=list)
    raw_signals: list[dict] = field(default_factory=list)
    origin: str = "fixture"  # scanners | fixture

    def as_dict(self) -> dict:
        return {
            "founder": self.founder,
            "company_id": self.company_id,
            "label": self.label,
            "truncation_date": self.truncation_date.isoformat(),
            "signal_count": len(self.raw_signals),
            "event_count": len(self.events),
            "origin": self.origin,
        }


def _aware(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(v))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _scan(founder: str) -> list:
    """B's scanners, if they've landed. Any that hasn't is skipped, never fatal."""
    signals = []
    for name in SCANNERS:
        try:
            mod = __import__(f"sourcing.scanners.{name}", fromlist=["scan"])
            signals.extend(mod.scan(founder) or [])
        except Exception as exc:  # noqa: BLE001 - a missing scanner must not stop collection
            log.info("collect: scanner %s unavailable (%s)", name, exc)
    return signals


def _ingest(signals: list) -> list[Event]:
    from sourcing import bus

    events: list[Event] = []
    for raw in signals:
        try:
            events.extend(bus.ingest(raw) or [])
        except Exception as exc:  # noqa: BLE001 - fall back to the fixture cohort
            log.info("collect: ingest unavailable (%s)", exc)
            return []
    return events


def collect(founder: str, truncation_date: datetime, **meta: Any) -> Footprint:
    """Gather a founder's footprint and cut it at truncation_date.

    The truncation happens HERE, at collection, as well as at read time via as_of. Two
    independent cuts, because this is the claim the whole pitch rests on.
    """
    cut = _aware(truncation_date)
    fp = Footprint(
        founder=founder,
        truncation_date=cut,
        company_id=meta.get("company_id"),
        label=meta.get("label", "unknown"),
    )

    events = _ingest(_scan(founder))
    if events:
        fp.events = [e for e in events if e.observed_at <= cut]
        fp.origin = "scanners"
        dropped = len(events) - len(fp.events)
        if dropped:
            log.info("collect: truncated %d post-cutoff signal(s) for %s", dropped, founder)
        return fp

    member = _fixture_member(founder)
    if member:
        fp.raw_signals = [
            s
            for s in member.get("signals", [])
            if "observed_at" not in s or _aware(s["observed_at"]) <= cut
        ]
    return fp


def load_cohort() -> dict:
    """Winners + matched controls + at least one known failure.

    Controls are what make the H12 fame check meaningful: comparable founders from the
    same era who did not break out. A cohort of winners alone proves nothing.
    """
    import json

    if not SEED_PATH.exists():
        raise LookupError(f"no backtest cohort at {SEED_PATH} — collection is a manual H1 task")
    blob = json.loads(SEED_PATH.read_text())

    members = list(blob.get("cohort") or [])
    # Tolerate the split-list shape too; the fixture is written on another branch.
    for key, label in (("winners", "winner"), ("controls", "control"), ("failures", "failure")):
        for m in blob.get(key) or []:
            members.append({**m, "label": m.get("label", label)})

    if not members:
        raise LookupError("backtest cohort is empty")
    return {"threshold": _threshold(blob), "members": members, "policy": _policy(blob)}


def _threshold(blob: dict) -> float:
    """The cohort states its threshold as an object — {value, axis, policy} — which is
    the better shape: a bare number does not say what it applies to. Assuming a float
    raised TypeError, /backtest degraded to the fixture without saying so, and the
    calibration page showed seeded numbers while looking like a live replay."""
    raw = blob.get("threshold", 0.6)
    if isinstance(raw, dict):
        raw = raw.get("value", 0.6)
    return float(raw)


def _policy(blob: dict) -> str | None:
    raw = blob.get("threshold")
    return raw.get("policy") if isinstance(raw, dict) else None


def _fixture_member(founder: str) -> dict | None:
    try:
        for m in load_cohort()["members"]:
            if str(m.get("founder", "")).lower() == founder.lower():
                return m
    except LookupError:
        return None
    return None
