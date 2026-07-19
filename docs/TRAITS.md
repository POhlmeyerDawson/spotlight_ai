# TRAITS — what we score, and why a source is not one

**Status: design, with a proven mapping.** The machine-readable companion is [`data/traits.json`](../data/traits.json); the mapping and attribution code is [`intelligence/traits.py`](../intelligence/traits.py); the discrimination evidence is [`tests/test_traits.py`](../tests/test_traits.py).

Read [SHARED.md](../SHARED.md) and [SOURCES.md](SOURCES.md) first. This document is subordinate to both.

This pass deliberately does **not** rewrite the scoring pipeline, and does not touch `memory/score.py` or `intelligence/flags.py`. It reorganises the *explanation* and proves the reorganisation discriminates. §5 states exactly why that restraint is load-bearing rather than laziness.

---

## 0. The decision

The question was whether each **source** should contribute one variable to the scoring formula, or whether sources should evidence secondary **traits** — openness to new technology, willingness to rapidly iterate — with multiple sources supporting each trait.

**Decision: score on traits, attribute by source.**

The reasoning, which must survive any future edit to this file:

> A source is a **collection channel, not a quality**. Nobody is good "at GitHub". Making a source a scored variable measures *where someone happens to be visible*, which is precisely the visibility term `graph.hidden_ranking` already subtracts, and precisely the Type 6 failure mode this product exists to avoid — a founder with real substance on Qiita, Gitee or a self-hosted GitLab and a thin GitHub presence would be ranked down for their geography wearing a technical mask.

The same fact expressed structurally: a source-as-variable model has no way to represent *"this founder is strong on iteration and we learned it from a regional platform"*. It can only represent *"this founder is strong on GitHub"*, which is a sentence about GitHub.

There is a second, quieter reason. Per-source variables cannot be triangulated. Two variables named `github` and `hn` are two numbers; two *sources* evidencing one trait are a claim and a witness. §3 is entirely about the difference.

---

## 1. The taxonomy, derived not invented

Seven traits. They are the honest generalisation of two things that already exist — the thematic groupings `intelligence/flags.py` already uses to organise its 34 rules, and the technical / soft-business split `data/sources.json` already draws across its 20 signals. Nothing here is a fresh ontology, and where the two disagreed the rules won, because the rules are what actually runs.

| Trait | Plain-language definition | Absence | min channels |
|---|---|---|---|
| `ships_to_users` | Turns work into something a stranger can install and run — and puts it in front of people who did not have to be nice about it. | CONDITIONAL | 1 |
| `iteration_velocity` | Returns to one artifact and keeps improving it over calendar time, rather than starting five new things. | CONDITIONAL | 1 |
| `engineering_rigor` | Does the unglamorous work that only pays off later: tests, CI, docs for users, deliberate versioning. | UNKNOWN | 1 |
| `technical_depth` | Genuine command of a hard problem, measured *against what they are attempting*. Depth is only depth if the problem is deep. | CONDITIONAL | 2 |
| `problem_scoping` | Takes a vague brief and makes it decidable: names non-goals, states assumptions, quotes measured numbers instead of adjectives. | UNKNOWN | 2 |
| `learns_from_failure` | Names a specific mistake at their own cost and changes course because of it. | UNKNOWN | 2 |
| `responds_to_scrutiny` | How they behave when a stranger says they are wrong, in public, on the record. | UNKNOWN | 1 |

`data/traits.json` carries, for each: the investor-facing phrasing, which sources can evidence it and *how*, the absence predicate, a gaming analysis paired with what makes gaming expensive, and the rule and registry-signal ids that back it.

### Why these seven and not five or twelve

`flags.py` groups its rules under six comment headers: shipping & cadence, iteration on the same artifact, learning from failure, ambiguity → concrete scoping, technical depth relative to the problem, users touching the artifact, plus the proof-protocol block. Two of those needed splitting and one needed merging:

- **"Shipping & cadence" splits.** *Having shipped at all* and *having kept shipping* are different claims about a person, and the second is the one that costs calendar time. Ten rules sat under one header measuring two things, so they became `ships_to_users` and `iteration_velocity` — and the five-founder profiles in §6 separate on exactly this seam, which is the empirical case for the split.
- **`engineering_rigor` splits out of the same header.** Tests, CI, docs and semver measure willingness to do unrewarded work, not shipping. `data/sources.json` already treats `test_discipline` as its own signal at its own magnitude.
- **"Users touching the artifact" merges away.** `show_hn_ship` and `external_contributors` are evidence of shipping to users; `answers_technical_questions` is conduct. Splitting a trait to preserve a comment header would have been the ontology inventing itself.
- **The proof-protocol block is not a trait.** It is a *source* — the highest-quality one we have, because we author the task. Its five rules distribute across the traits they actually evidence. This is the taxonomy applying its own rule to itself: `proof_protocol` is a collection channel, and a "proof performance" trait would have been a source wearing a trait's clothes.

### Every rule maps — and one deliberately does not

33 of the 34 rules in `flags.py` map to exactly one trait. Multi-mapping was rejected: a rule split across two traits dilutes attribution and makes the collapse in §5 stop being an identity, and every candidate for it (`repeat_shipper`, `burst_with_substance`) had a defensible dominant reading.

One rule maps to nothing, and that is a **finding, not a gap to be filled**:

> **`proof_fast_start`** — *"Started building within 30 minutes of receiving it?"* (weight 1.0)
>
> This measures reaction latency, not a quality of the builder. How fast someone can clear their calendar is a property of their calendar. SOURCES.md already bans off-hours and weekend commit activity as a signal, in bold, because *"it is a proxy for having no caregiving responsibilities"* — a 30-minute response window is the same proxy at a shorter timescale and arguably a sharper one. Inventing an "eagerness" or "responsiveness" trait to house it would be the taxonomy laundering a bias into respectability.
>
> **Disposition:** left mapped to nothing. It still fires and still enters the scalar through the existing rule-weight mass, so this module changes no score. Recommendation to whoever owns `flags.py`: drop it, or re-anchor it to when the founder *started* rather than when we *sent* it. `tests/test_traits.py::test_proof_fast_start_is_the_declared_unmapped_rule` fails if someone quietly gives it a home, so the argument has to be had again rather than lost.

### Signals with no rule behind them

The reverse check is less flattering. Several `data/sources.json` signals have no `flags.py` rule and therefore cannot currently evidence any trait: `users_actually_served` (dependents), `user_support_conduct`, `hiring_and_collaborator_retention`, `breaking_change_communication`, `peer_selection`, and **`maintenance_after_launch`** — which SOURCES.md calls the best cost-to-signal ratio in the registry and the one soft signal whose absence is genuinely MEANINGFUL. The abandoned-demo detector is specified and not built. `coauthor_graph_edge` is absent by design and must stay that way; it feeds PPR only.

---

## 2. Absence: the load-bearing field

Carried per trait, with the same three values SOURCES.md defines. The distinction being encoded:

> *A designer with no GitHub is not a red flag. An infra founder claiming a shipped distributed system with no code anywhere is.* Same missing data, opposite conclusions, and the difference is entirely whether **the founder's own claim implies the artifact should exist**.

So the CONDITIONAL traits carry an explicit predicate, and the predicate is always about the founder's claim rather than about the founder's field:

- `ships_to_users` — meaningful iff the claim implies a public artifact (a shipped product, users served).
- `iteration_velocity` — meaningful iff any public code artifact exists at all. Code touched once is information; no code that was never claimed is not.
- `technical_depth` — meaningful iff the claim implies hard technical work: a compiler, a scheduler, a distributed system, a quantization scheme.

Four traits are UNKNOWN and two of those are hard-coded so. `responds_to_scrutiny` in particular: *silence on HN says nothing about a founder*. Many founders have never been publicly criticised, and reading that as a negative turns the system into a detector for one professional subculture.

Nothing in this module converts an absence into a subtraction. `TraitProfile.vector()` returns `None`, not `0.0`, for a trait with no applicable rules — the cold-start founder in §6 returns `None` on all seven and collapses to the uninformative prior, which is the correct reading of "we could not say" and is asserted by test.

---

## 3. Triangulation

**A trait evidenced by one source is weaker than the same trait evidenced by three independent ones.** This is the main thing the trait model buys that a per-source model structurally cannot.

**Two signals from the same source are not independent.** GitHub commits and GitHub releases are the same platform, the same identity, the same person's unilateral control. A GitHub release and an HN thread are independent, because the second requires strangers who cannot be co-opted. Channels are therefore counted as **distinct sources**, not as distinct signals. Tensorpage fires seven rules on `iteration_velocity` and scores **one** channel for it; the test asserting this exists so nobody later "improves" corroboration by counting signals.

**Self-attested channels never corroborate.** A deck is founder-authored prose with no third party in the loop, and a `manual` event is us. Both can be the first voice, never the second. This one was found empirically rather than assumed: the Type 5 adversary (Synthgrid) cleared the two-channel gate on `problem_scoping` in the live corpus using deck + GitHub, because the *same keyword stuffing appears in the pitch and in the repo description*. Two surfaces, one person, counted twice. Excluding self-attested channels drops the adversary back below the gate and moves no legitimate founder.

The honest statement of the limitation: this rule is about **who controls the channel**, and GitHub is founder-controlled too. The real fix distinguishes founder-authored artifacts from third-party reactions *within* a source — a README versus a stranger's issue — and needs the `source_id` passthrough from SOURCES.md §7. Until then, channel counting is coarser than the concept it implements, and the nine registry sources that collapse onto the `web` enum value are undercounted as one channel.

**The curve.** Independent channels → confidence multiplier: 1 → 0.6, 2 → 0.8, 3+ → 1.0. Concave, because the third witness adds less than the second. This is a design judgement, argued not measured, and should be challenged on the argument.

**Corroboration multiplies confidence only — never the score.** If it touched the score it would reach the scalar and move every founder's band. `test_corroboration_never_reaches_the_score` enforces this.

**`observed` vs `evidenced`.** A trait is *evidenced* when any rule fires, and *observed* only when independent channels meet the trait's `min_channels`. The three cheap-to-game traits require two: `technical_depth`, `problem_scoping` and `learns_from_failure` rest largely on keyword matches over text the founder wrote, and one channel of that is a claim, not an observation. Traits whose evidence costs calendar time or third-party effort count at one, because the single channel already carries the cost.

`technical_depth` was initially set to 1 and the measurement in §6 corrected it: the Type 5 adversary scored *higher* on `technical_depth` than the legitimate fast-builder control, purely on keyword-stuffed deck copy, and at `min_channels: 1` it cleared the `observed` gate. Three of its five rules (`infra_domain_depth`, `benchmarks_published`, `explains_tradeoffs`) are regex matches and vocabulary is purchasable. This is the taxonomy applying its own stated rule to a trait that had been, wrongly, exempted from it — and it is the one place in this pass where running the numbers changed a design decision rather than confirming it.

---

## 4. Per-source attribution

The owner asked to show why each source **adds to or subtracts from** a founder's assessment. Under a trait model that becomes the **marginal contribution of a source's evidence to the traits it touched** — *"GitHub contributed +8 to iteration-velocity and +3 to technical-depth"*.

It is computed as a genuine **leave-one-source-out marginal**: re-run the rules with that source's events removed, and take the difference in trait score. Not asserted, not a caption on a number that was never recomputed.

```
SourceContribution:
  source              # schema.events.Source; -> data/sources.json source_id when §7.3 lands
  trait_id
  delta_points        # (score_with - score_without) * 100. SIGNED.
  rules_fired[]       # which rules this source's evidence actually fired
  evidence_event_ids[]# -> Event.event_id, so a bar drills to a quoted span
  sole_channel        # see below
```

This composes with, rather than replaces, the `SourceContribution` shape in SOURCES.md §4: that one carries fetch counts, coverage status and citations; this one carries the trait deltas that shape needs in order to draw a signed bar.

**Three properties worth stating.**

**The counterfactual is "we never looked", not "we looked and found nothing".** Removing a source also removes any rule it gated, because `flags.py` skips rules whose required source is absent — so the denominator moves too. That is the honest meaning of a source's marginal contribution, and SOURCES.md §4 already insists the two states be displayed differently (`NOT_ATTEMPTED` vs `SEARCHED_EMPTY`).

**Sources genuinely subtract.** Looking at GitHub *lowers* Tensorpage's `ships_to_users` read by 28.6 points, because it brings `external_contributors` into scope and that rule does not fire. This is not a bug to be smoothed away — it is the requested feature. A source that only ever adds is a source we are not reading honestly, and `test_attribution_can_be_negative` fails if that stops being true.

**`sole_channel` must not render as a bar.** When removing a source leaves a trait with no applicable rules, the delta is not a movement against a comparable baseline — it is the whole trait. Zaryad's `iteration_velocity` shows GitHub at +88.9 with `sole_channel=True`; the correct UI string is *"GitHub is the only reason this trait could be assessed"*, not an enormous bar. Rendering single-channel fragility as a large source effect would reintroduce visibility-as-quality through the UI, having just removed it from the model.

---

## 5. The bridge to the existing filter

`memory/score.py` consumes **one scalar**. The v1 collapse is deliberately an **identity, not a re-tuning**:

> A trait's collapse weight is the total weight mass of the `flags.py` rules mapped to it, **restricted to the rules that were applicable for this founder**.

With that choice, `Σ(wₜ · yₜ) / Σ(wₜ)` is algebraically the weighted YES-rate that `flags.observation()` already produces. Verified to 1e-12 on all five founders in §6, against both the fixtures and the live store.

**Why this weighting and no other.** Any other weighting is a re-tuning of the sensor wearing a taxonomy change as a disguise. It would shift every founder's `mu` and `band` with no calibration run behind it. The band is currently one of the few genuinely honest numbers in this system — it took real work to make it fall monotonically with evidence on irregularly spaced histories, and it is what carries the caveat on proof results — and it must not be destabilised under time pressure by a change whose actual purpose was to organise an explanation. Re-weighting the traits is a separate, deliberate, calibrated act. It is not a side effect of naming things.

**What collapsing loses, stated plainly.** The scalar carries **no per-trait uncertainty**. A founder observed on one trait across four independent channels and a founder observed on four traits from a single channel can collapse to the same `y_t`. Every corroboration count, every channel, and every `observed` flag survives only in the `TraitProfile` — which is why the profile is stored alongside the scalar rather than replaced by it, and why the UI should read the profile rather than re-deriving anything from `mu`.

**What a multivariate state would require — described, not built.** The filter's state would go from `[level, velocity]` to `[level₁..level₇, velocity₁..velocity₇]`, and:

- **`H` stops being `[[1, 0]]`.** Each observation touches only the traits whose rules were applicable, so the observation matrix becomes per-observation and rectangular. Every rule-applicability decision becomes a filter input.
- **`R` stops being a scalar.** Traits sharing a source have correlated noise — the off-diagonals are exactly the §3 independence structure, and setting them to zero would re-import the double-counting the channel rules exist to prevent.
- **`Q` acquires cross-trait terms.** Traits do not drift independently; someone learning to ship also tends to start iterating.
- **`P0` needs seven priors.** Currently one number, `MU0 = 0.5`, chosen because it is neutral. Seven neutral priors are not obviously neutral in aggregate.
- **Calibration multiplies.** `calibrate()` maps a yes-rate to the capability scale with four tuned constants fitted against the backtest cohort. That fit does not decompose per trait, and re-fitting seven of them needs a cohort with per-trait labels, which does not exist.
- **`FounderScore` and every consumer change shape.** `mu`, `band`, `trend` are scalars in the schema, in the API, and in the dashboard.

That is a calibration project, not an afternoon. Attempting it before H21 would trade the one honest band for seven unvalidated ones.

---

## 6. Does it discriminate?

This codebase has repeatedly produced code that looks implemented and measures nothing — a metric returning a confident 1.0 for everyone, a substance rule reading payload keys that did not exist and firing for nobody. So the taxonomy is not accepted on the strength of the argument above. **If every founder gets the same trait profile, it is decorative.**

Five founders across five archetypes, computed from real corpus events (`VCBRAIN_NO_SHIFT=1`, so the dates are the authored ones rather than demo-shifted):

| trait | vb-tensorpage<br>Visible Builder | cs-veritanode<br>Cold Start | intl-zaryad<br>Invisible Intl. | adv-synthgrid<br>Adversarial | adv-control-ferrite<br>Fast-builder control |
|---|---|---|---|---|---|
| `ships_to_users` | **0.71** [2ch] | — | 0.60 [1ch] | 0.00 [0ch] | **0.71** [2ch] |
| `iteration_velocity` | **0.89** [1ch] | — | **0.89** [1ch] | 0.22 [1ch] | 0.56 [1ch] |
| `engineering_rigor` | 0.40 [1ch] | — | 0.40 [1ch] | 0.20 [1ch] | 0.40 [1ch] |
| `technical_depth` | 0.64 [3ch] | — | 0.45 [2ch] | 0.43 [2ch raw, **1 indep.**] | 0.00 [0ch] |
| `problem_scoping` | 0.00 [0ch] | — | 0.33 [1ch] | 0.33 [1ch raw, **0 indep.**] | 0.00 [0ch] |
| `learns_from_failure` | 0.50 [1ch] | — | 0.00 [0ch] | 0.00 [0ch] | 0.00 [0ch] |
| `responds_to_scrutiny` | 0.00 [0ch] | — | n/a | n/a | 0.00 [0ch] |
| **collapsed scalar** | **0.589** | **0.500** | **0.549** | **0.213** | **0.327** |
| `flags.observation()` y | 0.589 | 0.500 | 0.549 | 0.213 | 0.327 |
| distinct sources | 4 | 1 (deck) | 3 | 2 | 2 |

`—` = no applicable rules, i.e. UNKNOWN. `n/a` = trait not evaluable for that founder. The collapse row matches the existing scalar exactly for all five.

Reproduced against the **live Postgres store** as well as the fixtures. Absolute values differ there and drift between runs — other workstreams are actively ingesting, so event counts moved from 97 to 119 for Tensorpage during this pass — but the collapse identity holds to 1e-12 on every run, and the ordering is stable: Tensorpage 0.625 > Zaryad 0.549 > Ferrite 0.404 > Veritanode 0.368 > Synthgrid 0.234. The table above is the fixture-derived one because it is reproducible and is what `tests/test_traits.py` asserts; the live store is a moving target by design right now.

**They discriminate, and in the directions the archetypes are supposed to represent.**

- **Cold start is UNKNOWN, not weak.** Veritanode returns `None` on all seven traits and collapses to the uninformative prior, 0.500 — *above* both adversarial founders. A deck with no public artifact reads as "we cannot say", which is the entire Type 2 beat. A source-as-variable model would have scored this founder near zero on every source variable and called it a low score.
- **The adversarial pair separates on the trait that costs calendar time.** Synthgrid and Ferrite look identical to a volume metric — Ferrite's burst is the *larger* of the two. They separate 0.56 vs 0.22 on `iteration_velocity`, because `burst_with_substance` fires for Ferrite and not for Synthgrid. This is the Type 5 guarantee, and the taxonomy locates it in a named trait rather than in an aggregate.
- **The international founder is scored on building, not on visibility.** Zaryad has three sources to Tensorpage's four and carries `transliterated_name` on every event, yet scores **identically** on `iteration_velocity` (0.89) and lands 0.549 against 0.589 overall. The remaining gap is `learns_from_failure` and `ships_to_users`, both traceable to specific missing artifacts rather than to thinness. The taxonomy has not learned geography.
- **Profiles vary *within* a founder, not just between them.** Tensorpage runs 0.89 on iteration and 0.00 on scoping; Ferrite runs 0.71 on shipping and 0.00 on depth. A profile where every trait scores the same would be a scalar wearing seven hats, and `test_traits_discriminate_within_a_founder_not_just_between_them` fails if that happens.

**Three honest weaknesses the same table exposes.**

**`engineering_rigor` barely discriminates** — 0.40 / 0.40 / 0.40 for three of the four evaluable founders, and 0.20 for the adversary. Its four rules are keyword and payload-flag presence checks, not the gating behaviour the signal definition calls for (*"weight CI-gating and test-changes-alongside-logic, not test file count"*). It currently separates almost nobody and should not be the trait carrying an assessment. This is recorded rather than papered over.

**The adversary's best trait is the cheapest one, and this measurement changed the design.** Synthgrid scores 0.43 on `technical_depth` — *higher than Ferrite, the legitimate fast builder, at 0.00* — because `infra_domain_depth` is a regex and Synthgrid's deck is keyword-stuffed by construction. The gaming analysis in `data/traits.json` predicted this; what it got wrong was the gate. At the originally-drafted `min_channels: 1`, Synthgrid **cleared `observed` on `technical_depth`**, so the containment story was false and would have shipped as a comment claiming otherwise. Raising the trait to 2 (§3) drops the adversary below the gate — its two raw channels, deck and GitHub, are one person's unilateral surfaces and reduce to one independent channel — while leaving Tensorpage (3ch) and Zaryad (2ch) observed.

Two things worth keeping from that. First: **the trait score is contained by the confidence machinery, not by the score itself.** Any consumer reading `score` without reading `observed` will be fooled by this founder. That is a hard constraint on the UI and the strongest argument in this document for eventually building the multivariate state, where the uncertainty travels with the number instead of alongside it. Second: the score itself is *not* where the Type 5 defence lives — Ferrite still beats Synthgrid overall (0.327 vs 0.213) on `iteration_velocity`, a trait that costs calendar time. Keyword traits are gameable and are gated; artifact traits are expensive and carry the separation. That division of labour is the taxonomy's actual claim, and it is the one the numbers support.

**`responds_to_scrutiny` fires for nobody in this cohort** — 0.00 where evaluable, n/a elsewhere. Its three rules need HN comment threads or proof-protocol behaviour, and the fixtures carry few. The most game-resistant trait in the taxonomy is currently the least evidenced, which is an argument for the Proof Protocol rather than against the trait.

---

## 7. Open items

1. **`source_id` passthrough** (SOURCES.md §7.3). Channels are keyed on the coarse `Source` enum, so nine registry sources collapse onto `web` and are undercounted as one channel. This is the single highest-value follow-up for this module.
2. **Independence within a source.** A README and a stranger's issue are both `github` but are not the same kind of evidence. Founder-authored vs third-party-witnessed is the distinction that matters, and it is finer than the source.
3. **Build `maintenance_after_launch`.** The specified abandoned-demo detector has no rule behind it, and it is the one soft signal whose absence is genuinely meaningful.
4. **Re-anchor or drop `proof_fast_start`** (§1).
5. **Strengthen `engineering_rigor`'s rules** to measure gating rather than presence, or accept that it is decorative and weight it accordingly (§6).
6. **Do not attempt the multivariate state before feature freeze** (§5).
