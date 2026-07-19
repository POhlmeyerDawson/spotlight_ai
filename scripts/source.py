"""Run the scanners over the real corpus and append what they find. Owner: B.

THE GAP THIS CLOSES. `sourcing/scanners/` has worked for a while — github, hn, arxiv and
web all make real network calls and all return live data. Nothing called them. The only
fan-out in the tree was `backtest/collect.py::collect`, which is reached from tests and
from `scripts/seed.py::cohort_profiles` (for the cohort's shape, not for scanning), and
there was no scheduler anywhere. The scanners were production code with no production.

Run it as a MODULE from the repo root — `python scripts/source.py` puts scripts/ on
sys.path instead of the repo, and `sourcing` stops being importable:

    uv run python -m scripts.source --subject "Solomon Hykes" --github karpathy
    uv run python -m scripts.source --corpus cohort --dry-run
    uv run python -m scripts.source --corpus store --scanner hn,web --refresh

EVERYTHING GOES THROUGH THE BUS. Signals are ingested with `bus.ingest()` and only then
appended with `store.append()`. There is no path in this file from a scanner to the store
that skips the bus, because the `<untrusted_content>` sanitizer lives in the bus and a
scanner's output is by definition attacker-influenced text (Invariant #4).

NO FIXTURE FALLBACK. If a scanner cannot reach the network it fails, loudly, and the run
reports a non-zero failure count. This is the opposite of `backtest/collect.py`, which
falls back to the hand-collected cohort on purpose — the backtest replays fixed history,
this command sources live.

GITHUB BUDGET. Unauthenticated GitHub is 60 requests/hour and a full `github.scan()` of
one login costs 2 + 4*MAX_REPOS. `--max-github` caps how many logins a single run will
fan out over, and the run prints what it actually consumed (see `bus.stats()`) rather
than leaving you to discover the ceiling by hitting it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

log = logging.getLogger("source")

# Scanners keyed by the argument they take. The distinction is the whole reason
# api/attest.py was broken: github.scan() wants a LOGIN, the rest take a free-text name.
NAME_SCANNERS = ("hn", "arxiv", "web")
HANDLE_SCANNERS = ("github",)
ALL_SCANNERS = NAME_SCANNERS + HANDLE_SCANNERS


def corpus_subjects(source: str) -> list[str]:
    """Founder names from the real corpus. Never a hardcoded demo list."""
    if source == "cohort":
        from backtest import collect

        names = []
        for m in collect.load_cohort()["members"]:
            founder = m.get("founder")
            if isinstance(founder, dict):
                names.append(founder.get("display_name") or founder.get("name_normalized"))
            elif founder:
                names.append(str(founder))
        return [n for n in names if n]
    if source == "store":
        from memory import store

        return [e["display_name"] for e in store.all_entities() if e.get("display_name")]
    raise SystemExit(f"unknown corpus {source!r} — use cohort or store")


def scan_one(scanner: str, subject: str, limit: int, refresh: bool) -> tuple[list, str | None]:
    """One scanner, one subject. Returns (signals, error). Never raises.

    A failure returns an error string instead of an empty list, because "found nothing"
    and "could not look" are opposite findings and the caller reports them differently.
    """
    from sourcing import bus

    mod = __import__(f"sourcing.scanners.{scanner}", fromlist=["scan"])
    original_ttl = bus.CACHE_TTL_SECONDS
    if refresh:
        bus.CACHE_TTL_SECONDS = 0.0  # every cache entry is stale -> every fetch is live
    try:
        return list(mod.scan(subject, limit=limit) or []), None
    except Exception as exc:  # noqa: BLE001 - one dead scanner must not end the run
        log.warning("%s FAILED for %r: %s", scanner, subject, exc, exc_info=True)
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        bus.CACHE_TTL_SECONDS = original_ttl


def ingest_and_store(signals: list, *, dry_run: bool) -> tuple[int, int]:
    """bus.ingest() every signal, then append. Returns (events, ingest_failures)."""
    from memory import store
    from sourcing import bus

    appended = failures = 0
    for raw in signals:
        try:
            events = bus.ingest(raw) or []
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log.warning("bus.ingest FAILED for %s: %s", raw.source_url, exc, exc_info=True)
            continue
        for event in events:
            if not dry_run:
                store.append(event)
            appended += 1
    return appended, failures


def run_research(subject: str, *, dry_run: bool) -> tuple[int, str | None]:
    """The agentic loop in `sourcing/research.py`, opt-in behind --research.

    It is bounded (rounds, fetches, wall-clock) but it costs LLM calls and real fetches
    per subject, which is why it is not on by default.

    `to_events()` goes through `bus.ingest` like everything else here. It did not used
    to, and the justification written in this docstring — "its text went through the
    loop's own redaction and citation machinery" — was wrong: that machinery is
    `redact_urls()`, which is URL-only by its own docstring and strips no injected
    instructions. The result was that a quoted span from a third-party web page reached
    `evidence_span` unsanitized and unflagged, and the module docstring's claim above
    that nothing here skips the bus was false for exactly this one path.
    """
    from memory import store
    from sourcing import research

    try:
        report = research.research(subject)
        events = research.to_events(report)
    except Exception as exc:  # noqa: BLE001
        log.warning("research FAILED for %r: %s", subject, exc, exc_info=True)
        return 0, f"{type(exc).__name__}: {exc}"
    for event in events:
        if not dry_run:
            store.append(event)
    return len(events), None


def load_candidates(
    cands: list, *, dry_run: bool, scan_depth: bool = True, limit: int = 25, derive: bool = True
) -> dict:
    """Land verified candidates as entities, companies and events.

    ORDER MATTERS. The entity is resolved FIRST, through `memory.resolver` at its own
    thresholds, because everything downstream hangs off the entity_id it returns. A
    string comparison here is what produced the six duplicate "Simon Willison" rows
    already in the store: two spellings of one person become two founders, each with
    half the evidence, and both then score at the prior for lack of it.

    An AMBIGUOUS resolution (the 0.40-0.85 band) is NOT merged and NOT dropped — the
    resolver already refused to guess, and overriding that refusal with a threshold of
    our own would defeat the point. Those are collected in `ambiguous` and reported.

    Every event goes through `bus.ingest` (Invariant #4). The signals come from the
    scanners, so `observed_at` is the source's own clock throughout (Invariant #1) —
    nothing here stamps a date.

    DERIVATION IS PART OF LOADING, NOT AN EXTRA. `memory/score.py` observes only
    GREEN_FLAG / PROOF_ARTIFACT / PROOF_BEHAVIOR — the DERIVED kinds. A scanner emits
    `repo_activity`, `hn_post`, `profile_fact`, none of which the scorer looks at. So a
    founder loaded without `core.pipeline.derive` sits at exactly the 0.5 prior with a
    0.5 band no matter how much real evidence was just collected for them, and the
    corpus grows while measuring nothing. That is not a hypothetical: the first batch
    of 25 loaded this way and every one of them scored 0.500/0.500 until derive ran.
    `validate=False` because the validator makes live LLM and search calls per claim;
    claims stay NOT_ATTEMPTED, which the memo reports honestly.
    """
    from memory import resolver, store
    from schema.events import CompanyProvenance, EntityCandidate, RawSignal, ResolutionStatus, Source
    from sourcing import bus
    from sourcing.scanners import github as gh

    out = {
        "entities": 0, "companies": 0, "events": 0, "merged": 0, "created": 0,
        "green_flags": 0, "ambiguous": [], "failures": {},
    }
    loaded_company_ids: list = []
    for cand in cands:
        try:
            resolution = resolver.resolve(
                EntityCandidate(
                    name=cand.display_name,
                    handles={"github": cand.github_login, "hn": cand.hn_author},
                    urls=[cand.repo_url, f"https://github.com/{cand.github_login}"],
                    source=Source.GITHUB,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not end the load
            out["failures"][cand.github_login] = f"resolve: {type(exc).__name__}: {exc}"
            continue

        if resolution.status == ResolutionStatus.AMBIGUOUS:
            out["ambiguous"].append(
                {
                    "login": cand.github_login, "name": cand.display_name,
                    "score": round(resolution.score, 3), "rationale": resolution.rationale,
                    "alternatives": [str(a) for a in resolution.alternatives],
                }
            )
            continue
        out["merged" if resolution.status == ResolutionStatus.MERGED else "created"] += 1
        entity_id = resolution.entity_id
        out["entities"] += 1

        # SOURCED, stated rather than defaulted. The old comment here argued that NOT
        # passing it was safer than hand-labelling — but the default it relied on was
        # itself a hand-label applied to every caller at once, including ones that had
        # not thought about it. These companies came off a live scanner reading the real
        # world, so the claim is true and now sits where a reader can check it.
        company_id = store.upsert_company(
            cand.company_name,
            provenance=CompanyProvenance.SOURCED,
            founder_entity_ids=[entity_id],
        )
        out["companies"] += 1

        meta = {"entity_id": str(entity_id), "company_id": str(company_id)}
        signals: list[RawSignal] = []

        # The launch itself. Real objectID, real created_at, both read from Algolia.
        signals.append(
            RawSignal(
                source=Source.HN,
                source_url=cand.hn_url,
                content=f"{cand.company_name} — Show HN launch, {cand.points} points",
                meta={
                    **meta,
                    "kind": "hn_post",
                    "observed_at": cand.launched_at,
                    "author": cand.hn_author,
                    "points": cand.points,
                    "object_id": cand.hn_object_id,
                    "repo": cand.repo_slug,
                    "evidence_span": f"Show HN: {cand.company_name} ({cand.repo_slug})",
                },
            )
        )

        # The longitudinal evidence: profile, repos, commits, releases. This is the
        # only expensive leg (~8 GitHub requests) and the only one that can show
        # sustained execution rather than a single dated launch.
        if scan_depth:
            try:
                for sig in gh.scan(cand.github_login, limit=limit):
                    sig.meta.update(meta)
                    signals.append(sig)
            except bus.RateLimited:
                out["failures"][cand.github_login] = "github rate limit — stopping"
                raise
            except Exception as exc:  # noqa: BLE001
                out["failures"][cand.github_login] = f"github: {type(exc).__name__}: {exc}"

        events, ingest_failures = ingest_and_store(signals, dry_run=dry_run)
        out["events"] += events
        if ingest_failures:
            out["failures"][f"ingest:{cand.github_login}"] = f"{ingest_failures} failures"
        loaded_company_ids.append((company_id, cand.github_login))

    if derive and not dry_run:
        from core import pipeline

        for company_id, login in loaded_company_ids:
            try:
                result = pipeline.derive(company_id, validate=False)
                out["green_flags"] += result["appended"].get("green_flag", 0)
            except Exception as exc:  # noqa: BLE001 - one company's rules must not end the load
                out["failures"][f"derive:{login}"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--subject", action="append", default=[], help="founder name (repeatable)")
    ap.add_argument("--github", action="append", default=[], help="github login (repeatable)")
    ap.add_argument("--corpus", default="cohort", help="cohort | store — used when no --subject")
    ap.add_argument("--scanner", default=",".join(ALL_SCANNERS), help="comma-separated subset")
    ap.add_argument("--limit", type=int, default=25, help="max signals per scanner per subject")
    ap.add_argument("--max-subjects", type=int, default=8)
    ap.add_argument("--max-github", type=int, default=2, help="logins to fan out over; 60 req/hr")
    ap.add_argument("--refresh", action="store_true", help="ignore the raw cache TTL entirely")
    ap.add_argument("--research", action="store_true", help="also run the agentic research loop")
    ap.add_argument("--dry-run", action="store_true", help="scan and ingest, do not append")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    ap.add_argument(
        "--discover",
        type=int,
        default=0,
        metavar="N",
        help="find N NEW verified founders via Show HN + GitHub and load them",
    )
    ap.add_argument(
        "--since",
        default="2023-06-01",
        help="--discover: earliest launch date to consider (YYYY-MM-DD)",
    )
    ap.add_argument(
        "--gh-budget",
        type=int,
        default=1200,
        help="--discover: cap on GitHub requests; the run stops rather than burning the key",
    )
    ap.add_argument("--no-depth", action="store_true", help="--discover: skip the github fan-out")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )

    from sourcing import bus

    bus.reset_stats()

    if args.discover:
        from datetime import datetime, timezone

        from sourcing import discover as disc

        since = int(
            datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        )
        cands, census = disc.discover(
            since_ts=since,
            want=args.discover,
            budget=args.gh_budget,
            exclude=disc.known_logins(),
        )
        loaded = load_candidates(
            cands, dry_run=args.dry_run, scan_depth=not args.no_depth, limit=args.limit
        )
        result = {"census": census, "loaded": loaded, "consumed": bus.stats()}
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"\n  discovered {census['admitted']} verified from {census['hits']} stories")
            for k, v in sorted(census.items()):
                print(f"    {k:<20} {v}")
            print(
                f"\n  loaded {loaded['created']} new + {loaded['merged']} merged entities, "
                f"{loaded['companies']} companies, {loaded['events']} events, "
                f"{loaded['green_flags']} derived green flags"
                f"{' [DRY RUN]' if args.dry_run else ''}"
            )
            print(f"    ambiguous (0.40-0.85, NOT merged): {len(loaded['ambiguous'])}")
            for a in loaded["ambiguous"][:10]:
                print(f"      {a['login']:<20} {a['score']}  {a['rationale'][:60]}")
            for t, e in list(loaded["failures"].items())[:10]:
                print(f"    FAILED {t}: {e}")
            c = bus.stats()
            print(f"    http: {c['requests']} requests, {c['cache_hits']} cache hits")
            for host, quota in c["quota"].items():
                print(f"    quota {host}: {quota}")
        return 0

    scanners = [s.strip() for s in args.scanner.split(",") if s.strip()]
    if unknown := sorted(set(scanners) - set(ALL_SCANNERS)):
        raise SystemExit(f"unknown scanner(s): {unknown}; known: {list(ALL_SCANNERS)}")

    subjects = (args.subject or corpus_subjects(args.corpus))[: args.max_subjects]
    logins = args.github[: args.max_github]

    report: dict[str, Any] = {
        "subjects": subjects,
        "github_logins": logins,
        "scanners": scanners,
        "dry_run": args.dry_run,
        "events": 0,
        "signals": 0,
        "failures": {},
        "per_scanner": {},
    }

    def record(scanner: str, target: str, signals: list, error: str | None) -> None:
        report["signals"] += len(signals)
        events, ingest_failures = ingest_and_store(signals, dry_run=args.dry_run)
        report["events"] += events
        bucket = report["per_scanner"].setdefault(scanner, {"signals": 0, "events": 0, "failed": 0})
        bucket["signals"] += len(signals)
        bucket["events"] += events
        if error or ingest_failures:
            bucket["failed"] += 1
            report["failures"][f"{scanner}:{target}"] = (
                error or f"{ingest_failures} ingest failures"
            )
        log.info(
            "%s %s -> %d signals, %d events%s",
            scanner,
            target,
            len(signals),
            events,
            f" [FAILED: {error}]" if error else "",
        )

    name_scanners = [s for s in scanners if s in NAME_SCANNERS]
    targets = 0
    for subject in subjects if name_scanners else []:
        targets += 1
        for scanner in name_scanners:
            signals, error = scan_one(scanner, subject, args.limit, args.refresh)
            record(scanner, subject, signals, error)

    for login in logins if "github" in scanners else []:
        targets += 1
        signals, error = scan_one("github", login, args.limit, args.refresh)
        record("github", login, signals, error)
    report["targets"] = targets

    if args.research:
        report["research_events"] = 0
        for subject in subjects:
            events, error = run_research(subject, dry_run=args.dry_run)
            report["research_events"] += events
            if error:
                report["failures"][f"research:{subject}"] = error

    report["consumed"] = bus.stats()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(
            f"\n  {report['events']} events from {report['signals']} signals "
            f"across {targets} target(s)"
            f"{' [DRY RUN — nothing appended]' if args.dry_run else ''}"
        )
        for name, bucket in sorted(report["per_scanner"].items()):
            print(f"    {name:<8} {bucket['signals']:>4} signals  {bucket['events']:>4} events")
        consumed = report["consumed"]
        print(
            f"    http: {consumed['requests']} live requests, "
            f"{consumed['cache_hits']} cache hits, {consumed['cache_expired']} expired, "
            f"{consumed['errors']} errors"
        )
        for host, quota in consumed["quota"].items():
            print(f"    quota {host}: {quota}")
        for target, error in report["failures"].items():
            print(f"    FAILED {target}: {error}")

    # A run where every scanner failed exits non-zero. It must not look like a run that
    # found nothing — that equivalence is the bug this whole workstream was about.
    return 1 if report["failures"] and not report["events"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
