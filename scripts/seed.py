"""Load the archetype fixtures into the event store as real Events. Owner: D.

The fixtures are events, never scores. Everything downstream reads the log, so a
pre-computed number here would be a number the pipeline never has to earn.

Idempotent: event ids are uuid5 of (company_id, index, observed_at), so a second run
appends nothing. Re-runnable against a live db without duplicating.

    uv run python scripts/seed.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.seed.provenance import provenance_for  # noqa: E402
from memory import store  # noqa: E402
from schema.events import Event, EventKind, Source  # noqa: E402
from sourcing.sanitize import sanitize_event  # noqa: E402

SEED_DIR = ROOT / "data" / "seed"
NAMESPACE = UUID("6f1a3c2e-9b47-4d51-a8e0-2c7f5b91d403")
END_OF_TIME = datetime(2999, 1, 1, tzinfo=timezone.utc)


def fixture_files() -> list[Path]:
    return sorted(SEED_DIR.glob("archetype_*.json"))


def event_uuid(company_id: str, index: int, observed_at: str) -> UUID:
    return uuid5(NAMESPACE, f"{company_id}|{index}|{observed_at}")


# Fixtures are authored on a fixed calendar, but the filter reads silence as decay:
# run them a year later and every founder correctly scores as dormant, which is right
# behaviour on stale data and a dead demo. Shifting the whole corpus so its newest
# event lands just before today keeps founders live while preserving every relative
# gap — and the gaps are what the trajectory is actually made of. Set VCBRAIN_NO_SHIFT=1
# to load the literal authored dates (the backtest fixture is never shifted).
SHIFT_HEADROOM = timedelta(days=9)


@lru_cache(maxsize=1)
def _shift() -> timedelta:
    if os.getenv("VCBRAIN_NO_SHIFT"):
        return timedelta(0)
    latest = max(
        (
            datetime.fromisoformat(e["observed_at"])
            for f in fixture_files()
            for profile in json.loads(f.read_text(encoding="utf-8")).get("profiles", [])
            for e in profile.get("events", [])
        ),
        default=None,
    )
    if latest is None:
        return timedelta(0)
    delta = (datetime.now(timezone.utc) - SHIFT_HEADROOM) - latest
    return delta if delta > timedelta(0) else timedelta(0)


def _parse(raw: str, shift: timedelta | None = None) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError(f"observed_at must be timezone-aware: {raw!r}")
    return dt + (_shift() if shift is None else shift)


def build_events(
    profile: dict[str, Any], ids: dict[str, UUID], shift: timedelta | None = None
) -> list[tuple[str, Event]]:
    """One profile -> (trace_ref, event) pairs, with entity/company ids already resolved.

    Refs stay anchored to the FIXTURE index, never to position in the output: sanitizing
    emits extra INTEGRITY events, and a positional ref would silently renumber every
    event after the first injection — breaking the trace drill-down that the demo clicks.

    Fixture text is authored UNSANITIZED on purpose (the adversarial deck really does
    carry an injection on slide 7) — detection is the pipeline's job, not the
    fixture's. This function is where that job gets done: seeding wrote Events
    straight to the store without ever passing them through the funnel, so the
    injection landed in production with integrity_flags=[] and there were zero
    INTEGRITY events in the whole database. Every seeded event now takes the same
    sanitizer every scanner takes, and the strip is recorded as a real detection.

    `shift` overrides the corpus-wide date shift. The backtest cohort passes
    timedelta(0): its dates ARE the claim ("we would have seen this in 2013"), so
    moving them forward to keep a demo warm would destroy the thing being tested.
    """
    slug = profile["company_id"]
    entity_id = ids[profile["founders"][0]["key"]]
    out = []
    for i, raw in enumerate(profile["events"]):
        observed = raw["observed_at"]
        event = Event(
            event_id=event_uuid(slug, i, observed),
            entity_id=entity_id,
            company_id=ids[raw.get("company", slug)],
            kind=EventKind(raw["kind"]),
            source=Source(raw["source"]),
            source_url=raw.get("source_url"),
            observed_at=_parse(observed, shift),
            payload=raw.get("payload", {}),
            evidence_span=raw.get("evidence_span"),
            confidence=raw.get("confidence", 1.0),
            integrity_flags=raw.get("integrity_flags", []),
        )
        clean, integrity = sanitize_event(event)
        # Deterministic ids keep the seed idempotent: a re-run must not duplicate the
        # INTEGRITY trace any more than it duplicates the event that produced it.
        for n, found in enumerate(integrity):
            out.append(
                (
                    f"{slug}#{i}!integrity{n}",
                    found.model_copy(
                        update={
                            "event_id": event_uuid(slug, i, f"{observed}|integrity|{n}"),
                            "entity_id": entity_id,
                        }
                    ),
                )
            )
        out.append((f"{slug}#{i}", clean))
    return out


def resolve_ids(profile: dict[str, Any], archetype: int) -> dict[str, UUID]:
    """Companies and entities first - upsert is keyed on name, so this is idempotent.

    Provenance is looked up per company name from the fixtures themselves rather than
    inferred from `archetype`. Those two agree for the archetype corpus and DISAGREE
    for the backtest cohort, which carries archetype None but contains both real
    companies and authored synthetic controls side by side — so inferring it here
    would label every synthetic control as sourced evidence.
    """
    ids: dict[str, UUID] = {
        profile["company_id"]: store.upsert_company(
            profile["company_name"],
            archetype=archetype,
            provenance=provenance_for(profile["company_name"]),
        )
    }
    for prior in profile.get("prior_companies", []):
        ids[prior["company_id"]] = store.upsert_company(
            prior["name"], archetype=archetype, provenance=provenance_for(prior["name"])
        )
    for founder in profile["founders"]:
        ids[founder["key"]] = store.upsert_entity(founder["name"], founder["name_normalized"])
    return ids


def cohort_profiles() -> list[dict[str, Any]]:
    """The backtest cohort, reshaped onto the same profile contract as the archetypes.

    The cohort has to enter the store through THIS function rather than a private
    path of its own. The backtest's whole claim is that it replays founders the same
    way live does; a bespoke loader would be the "special backtest mode" that the
    claim rules out. One shape in, one shape out.
    """
    from backtest import collect

    return [
        {
            "company_id": m["id"],
            "company_name": m["company_name"],
            "founders": [
                {
                    "key": f"{m['id']}-founder",
                    "name": m["founder"]["display_name"],
                    "name_normalized": m["founder"]["name_normalized"],
                }
            ],
            "events": m.get("events", []),
        }
        for m in collect.load_cohort()["members"]
    ]


def load() -> dict[str, Any]:
    existing = {e.event_id for e in store.events(as_of=END_OF_TIME)}
    per_archetype: dict[int, dict[str, int]] = {}
    resolved: dict[str, str] = {}
    event_refs: dict[str, str] = {}
    appended = skipped = 0

    def _ingest(profile: dict[str, Any], archetype: int | None, counts: dict[str, int]) -> None:
        nonlocal appended, skipped
        ids = resolve_ids(profile, archetype)
        resolved.update({k: str(v) for k, v in ids.items()})
        counts["companies"] += 1
        # The cohort's dates are the claim under test; never shift them (see build_events).
        shift = timedelta(0) if archetype is None else None
        for ref, event in build_events(profile, ids, shift):
            event_refs[ref] = str(event.event_id)
            if event.event_id in existing:
                skipped += 1
                continue
            store.append(event)
            existing.add(event.event_id)
            appended += 1
            counts["events"] += 1

    for path in fixture_files():
        fixture = json.loads(path.read_text(encoding="utf-8"))
        archetype = fixture["archetype"]
        counts = per_archetype.setdefault(
            archetype, {"label": fixture["label"], "companies": 0, "events": 0}
        )
        for profile in fixture["profiles"]:
            _ingest(profile, archetype, counts)

    cohort_counts = {"label": "Backtest cohort", "companies": 0, "events": 0}
    for profile in cohort_profiles():
        _ingest(profile, None, cohort_counts)
    per_archetype[0] = cohort_counts

    # Never write the id map when loading into a throwaway database. The test suite
    # calls load() against a tmp db, and writing here overwrote the real map with
    # UUIDs that exist only inside that test — silently breaking every API lookup
    # that resolves a slug, with `pytest` as the trigger.
    if os.getenv("VCBRAIN_DB_PATH"):
        return _summary(appended, skipped, per_archetype, event_refs)

    (SEED_DIR / "_resolved_ids.json").write_text(
        json.dumps(
            {
                "note": "Generated by scripts/seed.py. Maps fixture slugs and event_refs to the "
                "uuids in the event store. Regenerated on every run; safe to delete.",
                "companies_and_entities": resolved,
                "event_refs": event_refs,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return _summary(appended, skipped, per_archetype, event_refs)


def _summary(appended: int, skipped: int, per_archetype: dict, event_refs: dict) -> dict[str, Any]:
    return {
        "appended": appended,
        "skipped": skipped,
        "per_archetype": per_archetype,
        "companies": len(store.all_companies()),
        "entities": len(store.all_entities()),
        "event_refs": len(event_refs),
    }


def main() -> None:
    summary = load()
    print(f"seed: {len(fixture_files())} fixture files -> {SEED_DIR.relative_to(ROOT)}")
    for archetype in sorted(summary["per_archetype"]):
        row = summary["per_archetype"][archetype]
        print(
            f"  type {archetype} {row['label']:<24} "
            f"{row['companies']:>2} companies  {row['events']:>3} new events"
        )
    print(
        f"\n  {summary['appended']} events appended, {summary['skipped']} already present "
        f"(idempotent re-run)"
    )
    print(
        f"  store now holds {summary['companies']} companies, {summary['entities']} entities, "
        f"{summary['event_refs']} traceable event refs"
    )


if __name__ == "__main__":
    main()
