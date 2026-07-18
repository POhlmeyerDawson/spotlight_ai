# A — Memory, Entity Resolution & Founder Score

**You are the spine. Three other people are blocked on you at H3 and nowhere else. Ship the schema first, ship it ugly, refine after.**

Owns: `/schema/`, `/memory/`
Read [SHARED.md](SHARED.md) first — sections 3 and 4 are yours to publish.

---

## H0–1 — with everyone

- [ ] Lock the event schema in the room (§3 of SHARED). Push back hard on anything that can't be stamped with an honest `observed_at`.
- [ ] Take your 12 founders from the 50-label split. Label on **trajectory at time T**, not on outcome — you're producing training targets for a filter, and if you label with hindsight the H12 check fails.

## H1–3 — UNBLOCK EVERYONE (this is the only thing that matters this block)

- [ ] `schema/events.py` — Pydantic `Event`, `EventKind`, payload models per kind. Exactly as in SHARED §3.
- [ ] `schema/migrations/001_init.sql` — tables: `events`, `entities`, `companies`, `entity_aliases`, `merges`.
  - Index `(entity_id, observed_at)` and `(company_id, observed_at)`. Every read path hits these.
  - `events` gets no UPDATE or DELETE grant. Enforce append-only at the DB level, not by convention.
- [ ] `memory/store.py` — `append()`, `events()`. The `as_of` param is **required-by-default**: signature is `events(..., as_of: datetime)`, no `None` default. Make it hard to write the lookahead bug.
- [ ] `memory/api.py` — stub `resolve()` returning NEW-always, stub `founder()` returning `{mu: 0.5, band: 0.5, trend: 0.0}`. Real signatures, fake bodies.
- [ ] `core/llm.py` + `core/search.py` — the provider wrapper and Tavily client (SHARED §2a). ~60 lines total, and **everyone is blocked on it as much as on the schema.** Prompt cache to `data/llm_cache/` from the start.
- [ ] Seed 3 fake entities + ~20 events so B/C/D have something to read.
- [ ] **Merge to main. Announce in chat.** ← the H3 gate

## H3–8 — entity resolution

- [ ] `memory/resolver.py`. Four signals, scored and combined:
  - exact email / normalized URL match (github.com/x, x.github.io, twitter handle) → strong
  - name similarity: **Unicode-normalize, transliterate, then Jaro-Winkler** — Type 6 lives or dies here. Test with transliterated Cyrillic/Devanagari/Chinese-romanized name pairs before you call it done.
  - co-occurrence: shared repo, shared HN thread, co-authored paper → medium
  - temporal plausibility: same person can't commit from two identities in disjoint eras with zero overlap
- [ ] Three outcomes, and **the third is the point**: `MERGED` (>0.85), `NEW` (<0.4), `AMBIGUOUS` (between). Ambiguous is **never guessed** — it writes an `ENTITY_MERGE` event with `status=ambiguous`, and D surfaces it in the memo as "we're not sure these are the same person."
- [ ] `memory/queries.py` — as_of-scoped helpers C and D will need: latest facts, event timeline, claim set.
- [ ] Test: `tests/test_memory_asof.py` — insert events at t1<t2<t3, assert `events(as_of=t2)` never returns the t3 event. This test is your insurance policy.

## H8–12 — Founder Score (the Kalman filter)

`memory/score.py`. Local-linear-trend filter over the event history.

```
state  x_t = [μ_t, ν_t]        μ = capability level, ν = momentum
F = [[1, Δt], [0, 1]]          Δt in days since last observation, from observed_at
predict  x⁻ = Fx,  P⁻ = FPFᵀ + Q
update   on each observation y_t with noise r_t:
         K = P⁻Hᵀ(HP⁻Hᵀ + r_t)⁻¹ ;  x = x⁻ + K(y_t − Hx⁻) ;  P = (I − KH)P⁻
Score = μ   Band = √P[0,0]   Trend = ν (falls out structurally — you do not compute it separately)
```

- [ ] Observations come from C's green-flag reads: `y_t` = weighted YES-rate. **Take it as a typed input; don't reach into C's code.**
- [ ] Noise `r_t = r0 / self_consistency × source_penalty`. Proof Protocol events get low `r` (fresh, verified, behavioral) → they move the score hard. That's the demo moment.
- [ ] Contradicted claims (C's validator) never become observations. Filter them at the boundary in `score.py`, and log that you did.
- [ ] `contributing_event_ids` on every returned score — D's trace drill-down is built on this. No score is ever returned without its receipts.
- [ ] k-step forecast: propagate `P` forward k days → prediction interval. This is the "Area of Research 1" answer and it's free once the filter works.
- [ ] Calibrate `(q, r0)` on the 50 labels. Grid search is fine; this is not the place for elegance.
- [ ] `memory/score_fallback.py` — Beta-Binomial with forgetting factor λ. **Wire it behind a flag and verify the flag works at H10, not at H20.** If the Kalman misbehaves in the demo, you flip one env var.

## H12 — HARD GATE: fame vs trajectory

Run the backtest controls (D has them) through the score.

**If control founders — famous, but who didn't break out — clear the threshold, your score measures fame and the whole thesis is dead.** Stop everything else. Usual culprits: a green-flag that proxies for follower count, a source weight that rewards volume, an observation leaking post-breakout data past `as_of`. Fix it here or the demo is a lie.

Report the result in chat either way. A passed gate is worth saying out loud.

## H12–16
- [ ] Tune filter params against labels; sanity-check that band actually tightens as milestones land (D animates this).
- [ ] `as_of` support for the backtest: score any entity at any historical date, cold, from the event log alone.

## H16–18 — integration with everyone. H18–21 — fixes only. H21 — freeze.

---

## Definition of done
Any teammate can call `score.founder(entity_id, as_of)` and get back `{mu, band, trend, contributing_event_ids}` that (a) never saw the future, (b) decomposes to specific events, (c) is flat-to-negative on backtest controls and rising on backtest winners.

## Watch out for
- Making `as_of` optional "just for testing." It will end up in the demo path.
- Perfecting the resolver while the score isn't started. Resolver at 80% is fine; score at 0% is fatal.
- Computing trend as a diff of scores. It's `ν`, it's in the state vector, that's the whole reason you chose this model.
