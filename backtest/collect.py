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
    # Which scanners blew up, and why. "arxiv found nothing" and "arxiv raised" are
    # opposite findings — the first says the footprint is absent, the second says we did
    # not look — and an empty `events` list reported both identically.
    scanner_errors: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "founder": self.founder,
            "company_id": self.company_id,
            "label": self.label,
            "truncation_date": self.truncation_date.isoformat(),
            "signal_count": len(self.raw_signals),
            "event_count": len(self.events),
            "origin": self.origin,
            "scanner_errors": dict(self.scanner_errors),
        }


def _aware(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(v))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _scan(founder: str) -> tuple[list, dict[str, str]]:
    """B's scanners, if they've landed. Returns (signals, errors_by_scanner).

    A scanner failure is NOT fatal — partial collection beats none — but it is not
    invisible either. It was logged at INFO, which under default logging is below the
    threshold, so every scanner in the fan-out could raise and the run printed nothing
    and returned an empty list that read exactly like "this founder has no footprint".
    Failures now log at WARNING with a traceback and are returned to the caller.
    """
    signals: list = []
    errors: dict[str, str] = {}
    for name in SCANNERS:
        try:
            mod = __import__(f"sourcing.scanners.{name}", fromlist=["scan"])
            signals.extend(mod.scan(founder) or [])
        except Exception as exc:  # noqa: BLE001 - a failed scanner must not stop collection
            errors[name] = f"{type(exc).__name__}: {exc}"
            log.warning("collect: scanner %s FAILED for %s: %s", name, founder, exc, exc_info=True)
    return signals, errors


def _ingest(signals: list) -> tuple[list[Event], dict[str, str]]:
    from sourcing import bus

    events: list[Event] = []
    for raw in signals:
        try:
            events.extend(bus.ingest(raw) or [])
        except Exception as exc:  # noqa: BLE001 - fall back to the fixture cohort
            log.warning(
                "collect: ingest FAILED (%s) — falling back to fixtures", exc, exc_info=True
            )
            return [], {"bus": f"{type(exc).__name__}: {exc}"}
    return events, {}


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

    signals, errors = _scan(founder)
    events, ingest_errors = _ingest(signals)
    fp.scanner_errors = {**errors, **ingest_errors}
    if fp.scanner_errors and not events:
        log.warning(
            "collect: %s produced NO events and %d scanner(s) failed (%s) — the empty "
            "footprint below is a collection failure, not an absent footprint",
            founder,
            len(fp.scanner_errors),
            ", ".join(fp.scanner_errors),
        )
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

    # The cohort records its deprioritized failure as a single top-level object, not
    # inside a `failures` list — so it was never loaded as a member and the "show the
    # miss" slide came back empty. D.md calls that the most credible slide in the deck:
    # a backtest that only shows the winners it caught is a marketing document.
    failure = blob.get("correctly_deprioritized_failure")
    if isinstance(failure, dict) and not any(m.get("label") == "failure" for m in members):
        members.append({**failure, "label": "failure"})

    if not members:
        raise LookupError("backtest cohort is empty")
    members = [{**m, "company_id": m.get("company_id") or _resolve_company_id(m)} for m in members]
    return {"threshold": _threshold(blob), "members": members, "policy": _policy(blob)}


def _resolve_company_id(member: dict) -> str | None:
    """Look the member's company up in the event store, by name.

    The cohort file deliberately does NOT hardcode UUIDs: the ids are whatever the
    store minted when scripts/seed.py loaded the cohort, and a stale literal here
    would silently resolve to nothing — which is exactly the failure that let the
    replay fall through to hand-authored numbers while still calling itself a replay.
    Returns None when the cohort has not been seeded, and the caller reports that
    member as not replayed rather than substituting a fixture.
    """
    name = member.get("company_name") or member.get("name")
    if not name:
        return None
    try:
        from memory import store

        row = next((c for c in store.all_companies() if c.get("name") == name), None)
    except Exception as exc:  # noqa: BLE001 - no store is "not replayed", never a fixture
        log.info("collect: company lookup unavailable for %s (%s)", name, exc)
        return None
    return str(row["company_id"]) if row else None


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


def _member_names(m: dict) -> set[str]:
    """Every name a cohort member answers to, lowercased.

    The cohort records its founder as an object ({display_name, name_normalized})
    so the seeder can create a real entity from it. Matching on `str(m["founder"])`
    stringified that dict and matched nothing.
    """
    founder = m.get("founder")
    names = [m.get("name"), m.get("company_name")]
    if isinstance(founder, dict):
        names += [founder.get("display_name"), founder.get("name_normalized")]
    elif founder:
        names.append(str(founder))
    return {str(n).lower() for n in names if n}


def _fixture_member(founder: str) -> dict | None:
    try:
        for m in load_cohort()["members"]:
            if founder.lower() in _member_names(m):
                return m
    except LookupError:
        return None
    return None
