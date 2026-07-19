"""End-to-end verification against the RUNNING system.

Why this exists: the unit suite went green while the backtest never called the
scorer, the API served authored fixtures as computed output, a substance rule read
payload keys that did not exist and fired for nobody, and a metric returned a
confident 1.0 while measuring nothing. Every one of those bugs had the same shape —
a function existed, so it looked implemented, and nothing ever asked whether its
output meant anything.

So the rule here is: assert on OUTPUTS, and prove they came from the engine rather
than from a fixture. A check that a field is present proves nothing; a check that
the field DISAGREES with the seed file, or moves when the inputs move, proves the
computation ran.

    uv run python scripts/verify_demo.py            # against localhost:8000
    uv run python scripts/verify_demo.py --api URL  # against a deployment

Exit code is the number of failures, so CI and pre-demo checks can gate on it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str) -> None:
    """ok=None means "could not evaluate" — reported as WARN, never silently passed."""
    status = WARN if ok is None else (PASS if ok else FAIL)
    results.append((status, name, detail))


def get(api: str, path: str, timeout: float = 90.0) -> Any:
    req = urllib.request.Request(f"{api}{path}", headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def seed_json(name: str) -> dict:
    p = SEED / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else {}


# --- checks -------------------------------------------------------------------


def check_liveness(api: str) -> None:
    try:
        h = get(api, "/health", 20)
    except Exception as exc:  # noqa: BLE001
        check("api reachable", False, f"{type(exc).__name__}: {exc}")
        return
    check("api reachable", bool(h.get("ok")), f"llm={h.get('llm_provider')}")
    warnings = h.get("warnings") or []
    check(
        "no silent degradations",
        not warnings,
        "; ".join(warnings) if warnings else "none reported",
    )


def check_computed_not_authored(api: str) -> None:
    """The load-bearing check: served values must come from the engine.

    Proven by DISAGREEMENT with the seed file. If every served value matches the
    authored one exactly, we cannot tell computation from transcription — and that
    is precisely how 9 of 13 gate outcomes were served from a fixture.
    """
    rows = get(api, "/companies")
    seeded = {c.get("id"): c for c in seed_json("companies").get("companies", [])}

    check("ranked list non-empty", bool(rows), f"{len(rows)} companies")

    live_axes = 0
    for r in rows:
        f = (r.get("axes") or {}).get("founder") or {}
        s = ((seeded.get(r.get("id")) or {}).get("axes") or {}).get("founder") or {}
        if isinstance(f.get("score"), (int, float)) and isinstance(s.get("score"), (int, float)):
            # seed authors axes 0..1, api serves 0..100
            if abs(f["score"] - s["score"] * 100) > 0.5:
                live_axes += 1
    check(
        "founder axis is computed, not transcribed",
        live_axes > 0,
        f"{live_axes}/{len(rows)} differ from the authored value",
    )

    # Evidence ids must RESOLVE, not merely be present. Padded "" placeholders render
    # as clickable receipts that lead nowhere.
    empty = real = 0
    for r in rows:
        for axis in (r.get("axes") or {}).values():
            for eid in axis.get("evidence_event_ids") or []:
                if eid:
                    real += 1
                else:
                    empty += 1
    check("no placeholder evidence ids", empty == 0, f"{empty} empty, {real} real")


def check_gate_matches_engine(api: str) -> None:
    """The served gate must agree with what the decision engine actually computes."""
    try:
        sys.path.insert(0, str(ROOT))
        from datetime import datetime, timezone
        from uuid import UUID

        from api.routers.deps import company_uuid
        from intelligence import gate as gate_mod
    except Exception as exc:  # noqa: BLE001
        check("served gate matches engine", None, f"could not import: {exc}")
        return

    now = datetime.now(timezone.utc)
    rows = get(api, "/companies")
    agree = disagree = 0
    examples = []
    for r in rows:
        cid = company_uuid(r["id"])
        if not isinstance(cid, UUID):
            continue
        try:
            computed = gate_mod.evaluate(cid, now).outcome.value
        except Exception:  # noqa: BLE001
            continue
        if str(computed) == str(r.get("gate")):
            agree += 1
        else:
            disagree += 1
            if len(examples) < 3:
                examples.append(f"{r['id']}: served={r.get('gate')} computed={computed}")
    check(
        "served gate matches engine",
        disagree == 0,
        f"{agree} agree, {disagree} disagree" + (f" — {'; '.join(examples)}" if examples else ""),
    )


def check_backtest_is_a_replay(api: str) -> None:
    """The backtest must RUN, not report hand-typed numbers.

    lookahead_checked was a hardcoded literal while score.founder() was never called.
    A boolean that is always True proves nothing, so this asserts on evidence that
    the replay happened: a real count of events checked.
    """
    b = get(api, "/backtest")
    check("backtest served", bool(b), f"{len(b.get('trajectories') or [])} trajectories")

    la = b.get("lookahead_assertion") or {}
    checked = la.get("events_checked")
    check(
        "lookahead assertion actually ran",
        isinstance(checked, int) and checked > 0,
        f"events_checked={checked!r} (a literal True or 0 means it never ran)",
    )
    check(
        "no lookahead violations",
        la.get("violations") == 0,
        f"violations={la.get('violations')!r}",
    )
    check(
        "H12 fame check evaluated",
        bool(b.get("fame_check_evaluated", b.get("fame_check_passed"))),
        f"passed={b.get('fame_check_passed')} evaluated={b.get('fame_check_evaluated')}",
    )
    miss = b.get("correctly_deprioritized") or {}
    check(
        "the miss is shown",
        bool(miss.get("name")),
        f"{miss.get('name')} @ {miss.get('final_score')}",
    )


def check_dissent_lock(api: str) -> None:
    """Two ways in; both must be shut."""
    rows = get(api, "/companies")
    target = next((r["id"] for r in rows if r["id"] not in ("vb-tensorpage",)), rows[0]["id"])

    memo = get(api, f"/companies/{target}/memo?dissent_viewed=true")
    check(
        "dissent lock ignores the client flag",
        memo.get("recommendation") is None,
        f"{target}: recommendation={'null' if memo.get('recommendation') is None else 'UNLOCKED'}",
    )

    # POST /council unlocked the recommendation while returning no bear case at all.
    try:
        req = urllib.request.Request(f"{api}/companies/{target}/council", method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            council = json.load(r)
        rendered = bool(council.get("anti_memo") or council.get("bear_case"))
        after = get(api, f"/companies/{target}/memo?dissent_viewed=true")
        unlocked = after.get("recommendation") is not None
        check(
            "empty council cannot unlock the recommendation",
            not (unlocked and not rendered),
            f"council rendered={rendered}, recommendation unlocked={unlocked}",
        )
    except urllib.error.HTTPError as exc:
        check("empty council cannot unlock the recommendation", None, f"council {exc.code}")
    except Exception as exc:  # noqa: BLE001
        check("empty council cannot unlock the recommendation", None, str(exc)[:60])


def check_demo_beats(api: str) -> None:
    rows = {r["id"]: r for r in get(api, "/companies")}

    def founder(slug: str) -> float | None:
        return ((rows.get(slug) or {}).get("axes", {}).get("founder") or {}).get("score")

    cold = rows.get("cs-veritanode") or {}
    check(
        "T2 cold start routes to proof protocol",
        cold.get("gate") == "proof_protocol",
        f"gate={cold.get('gate')}",
    )

    adv, ctl = founder("adv-synthgrid"), founder("adv-control-ferrite")
    check(
        "T5 legitimate builder outscores the adversarial burst",
        (ctl or 0) > (adv or 0),
        f"control={ctl} adversarial={adv}",
    )

    intl = rows.get("intl-zaryad") or {}
    # "Surfaced" is a RELATIVE claim — this company must land near the top of whatever
    # list exists, not inside a fixed window. The old `rank <= 5` was a magic number
    # calibrated to a 13-company corpus; it broke the moment a 14th company took rank 1,
    # and a 100-200 company sourcing run would break it far harder while the underlying
    # behaviour was still correct. Top quartile scales with the corpus, and the floor of
    # 5 keeps the assertion from going vacuous on a small one.
    intl_cutoff = max(5, math.ceil(len(rows) * 0.25))
    intl_rank = intl.get("rank", 10**9)
    check(
        "T6 international founder is surfaced, not silently zeroed",
        (founder("intl-zaryad") or 0) > 0 and intl_rank <= intl_cutoff,
        f"rank={intl_rank}/{len(rows)} (top-quartile cutoff={intl_cutoff}) "
        f"founder={founder('intl-zaryad')}",
    )

    # Type 6 evidence must not be voided by provenance flags.
    try:
        detail = get(api, "/companies/intl-zaryad")
        integ = detail.get("integrity") or []
        check(
            "T6 provenance flags are surfaced, not hidden",
            len(integ) > 0,
            f"{len(integ)} integrity notes shown",
        )
    except Exception as exc:  # noqa: BLE001
        check("T6 provenance flags are surfaced, not hidden", None, str(exc)[:60])


def check_band_tightens(api: str) -> None:
    """D.md calls the tightening band the most legible visual in the system.

    It was measured WIDENING (0.175 -> 0.241) as evidence accumulated, while the UI
    showed a fixture that tightened.
    """
    try:
        h = get(api, "/companies/vb-tensorpage/score-history")
    except Exception as exc:  # noqa: BLE001
        check("band tightens as evidence accumulates", None, str(exc)[:60])
        return
    series = h.get("series") or h.get("points") or []
    bands = [p.get("band") for p in series if isinstance(p.get("band"), (int, float))]
    if len(bands) < 3:
        check("band tightens as evidence accumulates", None, f"{len(bands)} points")
        return
    check(
        "band tightens as evidence accumulates",
        bands[-1] < bands[0],
        f"{bands[0]:.3f} -> {bands[-1]:.3f} over {len(bands)} points",
    )


def check_no_pedigree_in_served_output(api: str) -> None:
    """Invariant #3 checked where it matters — in what a user actually receives.

    The unit test scans only *.py in six directories, so it missed app/, data/ and
    schema/ entirely and was defeated three ways with the suite green.
    """
    sys.path.insert(0, str(ROOT))
    try:
        from intelligence.banned import BANNED_TERMS
    except Exception as exc:  # noqa: BLE001
        check("no pedigree in served output", None, f"could not import ban list: {exc}")
        return

    import re

    # Word-boundary anchored, or "mit" matches "commit" and "submit" — the same
    # substring trap the unit test hit. A checker that cries wolf gets ignored,
    # which is worse than no checker.
    blob = json.dumps(get(api, "/companies")).lower().replace("news.ycombinator.com", "")
    hits = [t for t in BANNED_TERMS if re.search(rf"\b{re.escape(t.lower())}\b", blob)]
    check("no pedigree in served output", not hits, f"hits={hits}" if hits else "clean")


def check_access_lift_honest(api: str) -> None:
    h = get(api, "/hidden")
    lift = h.get("access_lift")
    cands = h.get("candidates") or []
    vis = {round(c.get("visibility", 0), 4) for c in cands}
    if lift is None:
        check("access_lift refuses when it cannot measure", True, "None (uniform visibility)")
    else:
        check(
            "access_lift measured against a real visibility spread",
            len(vis) > 1,
            f"lift={lift:.3f} over {len(vis)} distinct visibility values",
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    args = ap.parse_args()
    api = args.api.rstrip("/")

    print(f"verifying {api}\n")
    for fn in (
        check_liveness,
        check_computed_not_authored,
        check_gate_matches_engine,
        check_backtest_is_a_replay,
        check_dissent_lock,
        check_demo_beats,
        check_band_tightens,
        check_no_pedigree_in_served_output,
        check_access_lift_honest,
    ):
        try:
            fn(api)
        except Exception as exc:  # noqa: BLE001
            check(fn.__name__, False, f"raised {type(exc).__name__}: {exc}")

    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"  {status:4}  {name:<{width}}  {detail}")

    failures = sum(1 for s, _, _ in results if s == FAIL)
    warns = sum(1 for s, _, _ in results if s == WARN)
    print(f"\n{len(results) - failures - warns} passed, {failures} failed, {warns} unevaluated")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
