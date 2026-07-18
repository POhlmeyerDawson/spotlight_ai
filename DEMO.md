# Demo script — 2.5 minutes

Every number below was read off the running system, not written from the plan. If a
number here disagrees with the screen, the screen is right and this file is stale —
re-run `make demo-check` (below) before trusting it.

---

## Pre-flight (do this 10 minutes before, not 1)

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000    # backend
cd app && pnpm build && pnpm start -p 3005                # frontend
curl -s localhost:8000/health | python3 -m json.tool      # read the warnings
```

`/health` reports the things that fail **quietly**. Check `warnings` is empty. If it
mentions the GitHub rate limit, live scanning will return nothing — which on stage
is indistinguishable from a founder having no footprint. Set `GITHUB_TOKEN` in
`.env` (60/hr → 5000/hr) and restart.

Open all four tabs before you start talking: `/`, `/pipeline`, `/company/intl-zaryad`,
`/backtest`.

---

## The beats

**0 · Thesis (15s)** — `/pipeline`, thesis panel.
> "Config, not code. Sectors, stage, geo, check size. Geography is deliberately
> unrestricted — a geographic filter is the cheapest way to systematically miss the
> founder we're about to show you."

**1 · Visible builder (25s)** — Tensorpage, **rank 1**, founder **74.0**, band **5.6**.
Click into the score → trace drill-down → a quoted span with its slide ID and URL.
> "Every number is clickable down to the text it came from. No score exists here
> without receipts."

**2 · Cold start — the centrepiece (35s)** — Veritanode, gate reads **`proof_protocol`**.
> "Deck only. No public footprint. Most systems either guess or abstain. We generate a
> challenge from this founder's own central technical claim — and we plant two things
> in it: one ambiguous requirement, and one constraint that's subtly wrong."

Show the planted constraint on the Proof Protocol panel. Then say the honest part:
> "The generator, the grader and the attestation are real. This completion is
> pre-run — we're telling you that rather than letting you wonder."

**3 · Serial founder (10s)** — Meshledger, founder **68.0**.
> "Same founder, second company. The founder score persists across the boundary; the
> opportunity score resets. Score it from either company and you get the same number."

**4 · Trust — pick one, keep the other as backup (25s)**
- *Contradiction*: Arcwell vs Tallwind — **identical $40,000 ARR claims**, opposite
  outcomes. Only the ordering differs. "If we flagged the second one, we'd be
  pattern-matching on words instead of reasoning about time."
- *Adversarial*: Synthgrid **37.8** vs the control Ferrite **42.2** — and **Ferrite's
  burst is bigger**. "We don't false-positive fast builders, and that's measured, not
  asserted." Injection caught on slide 7 is visible in the trace.

**5 · Backtest (25s)** — `/backtest`. **fame_check passed**, **hit_rate 0.75**,
8 trajectories.
> "Winners rise, controls stay flat and below the line. Three of four caught — and
> here's the fourth, the one we missed. Plus Veridian Stack at 38 against a 62
> threshold: correctly deprioritised. A backtest that only shows its winners is a
> marketing document."

**6 · Close — the equity thesis (20s)** — Zaryad Compute, **rank 2**, founder **73.2**.
> "Transliterated name, non-prestige institution, non-English sources. Second on the
> list. Earlier today this founder scored at the prior with zero evidence, because a
> transliterated name was silently disqualifying every event attached to them. That
> bug is exactly what this system exists to prevent, and we found it in our own code."

Point at the integrity flags on the page: surfaced, not hidden.

---

## Cut order if you're over time
Beat 3 (10s) → beat 0 (15s) → one half of beat 4. Never cut 2, 5 or 6.

## If something breaks
Every route falls back to fixtures and says so with a chip. A `FIXTURE DATA` chip on
screen is survivable — claiming live data while showing fixtures is not. If the
backend dies, say so and keep going; the page still renders.

## Say these out loud
- The Proof Protocol completion is pre-run.
- Backtest winners/controls are a curated cohort; the **replay** through the live code
  path is real, the cohort is hand-collected.
- Market and idea-vs-market axes are seeded; the founder axis is computed live. The
  `live` flag on each axis says which is which.
- `access_lift` currently reports **nothing**, not a number — visibility is uniform
  across the seeded corpus, so the metric cannot discriminate. It refuses rather than
  reporting a confident 1.0. Don't claim it.

## make demo-check
```bash
curl -s localhost:8000/health | python3 -m json.tool
curl -s localhost:8000/companies | python3 -c "import sys,json;[print(r['rank'],r['name'],r['axes']['founder']['score']) for r in json.load(sys.stdin)[:6]]"
curl -s localhost:8000/backtest  | python3 -c "import sys,json;d=json.load(sys.stdin);print('fame',d['fame_check_passed'],'hit',d['hit_rate'],'traj',len(d['trajectories']))"
```
