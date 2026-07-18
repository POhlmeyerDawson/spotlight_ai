# D — Experience, Memo, Backtest & Demo

**You are the integration lead. Two things decide whether we win: the backtest (proof the system would have found them) and the demo running clean. Both are yours. You are also the only person who will ever see the whole pipeline at once — when something's broken between two modules, you find it first.**

Owns: `/app/`, `/api/`, `/backtest/`, `/data/seed/`
Read [SHARED.md](SHARED.md) — §4 is what you mock against.

---

## Standing rule
**Never block on a teammate.** Every dependency gets mocked against its SHARED §4 signature the moment you need it. Swap the mock for the real call when their PR lands. If you're ever idle waiting for someone, you've done this wrong.

## H0–1 — with everyone
- [ ] Schema lock — you're the one who has to *render* every field, so speak up about anything unrenderable.
- [ ] **Own the demo script from hour zero.** Write the 2.5 minutes now, as prose. Everything you build gets judged against "does this beat need it." It will change; write it anyway.
- [ ] Your 12 labels.

## H1–3 — shell + start backtest collection (collection is slow — start it now)

- [ ] Next.js + Tailwind + shadcn scaffold. Routes: `/` (ranked list), `/company/[id]` (scorecard + trace), `/backtest`.
- [ ] FastAPI app in `/api` with routers matching SHARED §4, all returning fixtures.
- [ ] Thesis config UI — sectors, stage, geo, check size, risk appetite. **Config, not code.** Reads/writes one JSON blob. Cheap, and it opens the demo.
- [ ] **Start backtest data collection.** 3–4 winners + matched controls (comparable founders, same era, who didn't break out — controls are what make the H12 gate meaningful). Collect early-footprint URLs raw: pre-breakout GitHub, HN posts, papers. **Truncate sources to pre-breakout by hand and record the truncation date.** This is tedious and it's the single most persuasive artifact we have. Do not delegate it to the last block.

## H3–8 — scorecard, trace, archetypes

- [ ] Scorecard: three axes side by side, each with score + trend arrow + confidence band + evidence count. **Never a single blended number on screen.**
- [ ] **Trace drill-down** — click any score → contributing events → source span → original URL/slide ID. This is the "receipts" claim made literal. Judges will click it. Make sure it goes all the way down to a quoted span, not just a source name.
- [ ] Seed all 12+ archetype profiles in `data/seed/` as JSON:
  | # | Type | Must contain |
  |---|---|---|
  | 1 | Visible Builder | rich GH/HN footprint, found outbound |
  | 2 | **Cold Start** | deck only, zero public signal → Proof Protocol |
  | 3 | Serial Founder | prior-company events + new company |
  | 4 | **Contradiction** | deck "$40K ARR" + public post "pre-revenue", **different timestamps** |
  | 5 | **Adversarial** | keyword-stuffed deck, 3k-commit burst, injection on slide 7 |
  | 6 | **Invisible International** | transliterated name, non-prestige institution, non-English source |
  Types 2, 4, 5, 6 are the ones that go on stage — build those first and richest.

## H8–12 — memo generator + H12 GATE

- [ ] `api/memo.py` — five required sections: Thesis · Founder · Market · Risks · Recommendation.
  - **Gaps are flagged, never filled.** "No independent revenue verification attempted" is a feature. A memo that fabricates to look complete loses the trust criterion entirely, and judges specifically look for this.
  - Every claim inline-cites its event ID.
  - Ambiguous entity resolutions surface as "we could not confirm these are the same person."
- [ ] **H12 HARD GATE with A:** run backtest controls through the score. Controls must NOT clear threshold. If they do, the score measures fame — everything stops until it's fixed. You run the check; A fixes it.

## H12–16 — the backtest rig (with A + C)

`/backtest/`. This is proof #1 of the whole pitch.

- [ ] Replay: truncated historical sources → S2 ingestion → S9 recommendation, with `as_of` pinned to a date **before** the founder was known. Uses the same code path as live. If it needs a special mode, it isn't a backtest.
- [ ] **Assert no lookahead** in the rig itself: any event with `observed_at > as_of` reaching the scorer raises. Loudly. This assertion is what makes the claim credible rather than asserted.
- [ ] Calibration report page: winners' score trajectories rising vs controls flat, threshold line, hit rate, and **one failure the system correctly deprioritized.** Show the miss too — it's the most credible slide in the deck and it costs nothing.

## H16–18 — INTEGRATION CHECKPOINT (you run this)
Full pipeline, Type 1 + Type 2, end to end, real modules, no mocks. Whatever's broken gets **cut, not fixed** — you make that call.

## H18–21
- [ ] **Memo vs dissent split view** — side by side, recommendation locked until dissent is opened. The UX identity, nearly free, and no other team will have it.
- [ ] Moving score line + tightening confidence band as events land. The single most legible visual of the whole system — a score that *moves* explains the state-space model without a word of explanation.
- [ ] NL compound query over the ranked list ("infra founders with rising trend and unverified revenue").
- [ ] Wire all six archetype paths.

## H21 — FEATURE FREEZE. You call it. No exceptions.

## H22–24
- [ ] Dry-run ×3 against all six archetypes. Time it. If it's over 2:30, cut a beat — don't talk faster.
- [ ] **Record the backup video.** Do this at H22, not H23:45. ElevenLabs credit is available if you want a clean narration track instead of recording voice at hour 23 — worth ~15 minutes, not more. Live demos fail and the video is the difference between a bad five minutes and no demo.
- [ ] Load the demo page in the browser and leave it there. Disable notifications. Check the projector resolution.

## Demo order (2.5 min)
thesis config → **Type 1** (outbound found them + score with receipts + moving line) → **Type 2** (Proof Protocol, the centerpiece) → **Type 4 or 5** (pick one live, one as backup) → Type 3 (10s, persistence) → backtest calibration → **Type 6 + access_lift close**.
Score-yourself-in-the-room only if you're ahead. First thing cut.

---

## Definition of done
A dashboard where every number is clickable down to a quoted source span; a memo that flags what it doesn't know; a backtest that replays history through the live code path with no lookahead; and a 2.5-minute demo you've run three times without touching the keyboard except where you meant to.

## Watch out for
- Polishing UI before the backtest exists. The backtest is unskippable; UI polish is cut item #8.
- Leaving backtest source collection until H12. It's manual and slow — H1.
- A demo that depends on four services being simultaneously healthy. Pre-warm everything, cache what you can, and know which beat you drop if something dies.
