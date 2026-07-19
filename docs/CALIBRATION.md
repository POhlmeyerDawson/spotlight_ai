# Calibration

What the founder score's accuracy claims actually rest on, and — more of this document —
what they do not.

The governing rule here is the one `intelligence/conformal.py` already set: **report the
statistics the sample size can support, and refuse the ones it cannot.** Every refusal
below is a code path that returns a reason instead of a number, not a paragraph of prose
with nothing behind it. `backtest/crossval.py:REFUSALS` and `backtest/fairness.py:Refused`
are where they live, and `tests/test_backtest.py` asserts each one refuses.

---

## 1. What the cohort is

Twelve labelled members, replayed through `memory.score.founder()` at twelve cutoffs each.

| Kind | n | Evidence |
|---|---|---|
| Winners | 4 | Real companies, reconstructed from public GitHub/HN/arXiv history |
| Synthetic controls | 4 | **Composed.** Same author as the winners |
| Real controls | 3 | Real companies, reconstructed from cited public sources |
| Deprioritized failure | 1 | **Composed** |

### The weakness the fixture states about itself

The four synthetic controls and the failure are invented composites, and **the same
author wrote both sides of the comparison.** The scorer produced the 0.78-vs-0.25
separation unaided — that part is real — but the evidence it separated was written by
someone who already knew which side should win. That is a much weaker test than real
non-breakout contemporaries, and no amount of downstream statistics repairs it.

---

## 2. The real controls

Three real non-breakout contemporaries were reconstructed from public sources. Each
event in `data/seed/backtest.json` carries a `verification` field marking every fact
`VERIFIED` (with the URL it came from), `UNVERIFIED-RECALL`, or `NOT-FOUND`.

| Control | Matched to | Outcome | Verified anchors |
|---|---|---|---|
| **Flynn** | Docker | Never raised a Series A; repo archived 2021-09-04, README `[UNMAINTAINED]` | repo created 2013-07-09; HN launch 339 pts (item 6058662); crowdfunding snapshot; preview release 216 pts (item 7622445) |
| **Deis** | Docker | No independent breakout; OpDemand → Engine Yard 2015-04, → Microsoft 2017-04 | repo created 2013-07-22; launch 131 pts (6167712); dated releases v0.1.0 / v0.5.1 / v0.8.0 / v0.9.0 |
| **Space Cloud** | Supabase | Final commit 2024-01-28, "now in maintenance mode" | repo created 2019-02-09; v0.13.1 2019-11-25; HN 251 pts (26997595) |

These matter because their **outcomes are facts about the world, not authorial choices.**
Flynn's founder wrote the postmortem himself; nobody arranged for Flynn to fail so that
this backtest would work.

### What could not be sourced, and what that costs

**Historical commit volumes, lines changed, contributor counts and star counts at a past
date are `NOT-FOUND`.** The GitHub API serves current state; period-accurate figures need
Wayback snapshots or an archival dataset. Those fields were **omitted rather than
estimated** — but the omission is not neutral, because they are inputs several scoring
rules read. Measured directly:

| Member | Raw events | Rules applicable | Rules fired |
|---|---|---|---|
| Docker (winner) | 15 | 28 | 28 |
| **Deis** (real control) | 10 | 28 | **9** |
| **Flynn** (real control) | 7 | 27 | **3** |
| **Space Cloud** (real control) | 3 | 28 | **4** |
| Northgate (synthetic control) | 9 | 27 | 2 |

The applicable-rule denominators are nearly equal, so the comparison is not structurally
rigged. But Deis fires exactly the shipping-discipline rules its verified release cadence
supports (`repeat_shipper`, `releases_same_repo`, `semver_discipline`,
`iterates_same_artifact`) and **cannot fire** the ones needing payload metadata
(`tests_present`, `ci_configured`, `external_contributors`) or thread bodies
(`explains_tradeoffs`, `states_assumptions`) — because those sources were not retrievable.
Part of every real control's gap is our archive access, not its trajectory.

`backtest/runner.py:_evidence_parity` computes this as a ratio against the matched
winner. Below `EVIDENCE_PARITY_FLOOR = 0.5` a control is reported but does not carry the
gate. Only **Deis (0.67)** clears it; Flynn (0.47) and Space Cloud (0.21) do not.

---

## 3. The H12 gate: `indeterminate`, not `PASS`

> **H12** — if controls clear the threshold, the score measures fame, not trajectory.

No replayed control cleared 0.62. The raw boolean `fame_check_passed` is `True`, and it
is preserved for the API contract — but it never travels without `fame_check.strength`,
which currently reads:

```
strength: "indeterminate"
```

Because the verdict decomposes into two arms and **neither establishes the gate**:

- **Synthetic arm** (n=4): passes. Weak — the author wrote both sides.
- **Real arm** (n=1, Deis only): passes. **A gate that turns on one company is a fact
  about that company.** `MIN_REAL_CONTROLS = 2`.

This is the same vacuous-truth failure the check already learned once, in a new shape: a
verdict that cannot fail is not a verdict.

### What a genuine H12 backtest requires

Enumerated in code at `backtest/runner.py:REAL_BACKTEST_REQUIREMENTS`, so the gap stays
visible in the artifact:

1. Real non-breakout contemporaries only.
2. Archival source access **at the control's own dates** — Wayback snapshots of the repo
   page, or GH Archive. Not today's values read backwards.
3. The **full text** of historical threads, not just their scores. Several rules judge
   what a founder wrote; a control reconstructed from titles and point counts is
   handicapped on precisely the axes the product claims to judge.
4. Enough controls per winner that one member landing either way does not move the gate.
5. Controls selected **before their scores are known**, by a rule stated in advance.

---

## 4. Cross-validation (`backtest/crossval.py`)

Leave-one-out is the only defensible scheme at this n. `conformal.py` already drops a
company from its own calibration set; this extends that from the conformal quantile to
the **whole decision rule**. Each fold refits the threshold on the other eleven members
and judges the held-out one with a threshold it had no part in choosing.

**Result: 12 of 12 correct.** Per fold — the average hides everything at this n:

| Held out | Label | Peak μ | Fold threshold | Predicted | Correct |
|---|---|---|---|---|---|
| Parallax Models | control | 0.2476 | 0.5530 | no | ✓ |
| Riverbend Data | control | 0.2833 | 0.5530 | no | ✓ |
| Hallmark Edge | control | 0.2833 | 0.5530 | no | ✓ |
| **Flynn** | control (real) | 0.2904 | 0.5530 | no | ✓ |
| Veridian Stack | failure | 0.2907 | 0.5530 | no | ✓ |
| **Space Cloud** | control (real) | 0.2952 | 0.5530 | no | ✓ |
| Northgate Runtime | control | 0.3150 | 0.5530 | no | ✓ |
| **Deis** | control (real) | 0.3510 | 0.5350 | no | ✓ |
| Vercel (ZEIT era) | winner | 0.7550 | 0.5619 | yes | ✓ |
| Hugging Face | winner | 0.7727 | 0.5530 | yes | ✓ |
| Supabase | winner | 0.7816 | 0.5530 | yes | ✓ |
| Docker (dotCloud) | winner | 0.7846 | 0.5530 | yes | ✓ |

### The number that actually matters: threshold indeterminacy

```
every threshold in (0.3510, 0.7550) classifies all 12 members identically
width: 0.4040
```

**The cohort determines the threshold only to within a 0.40-wide band on a 0..1 scale.**
The shipped 0.62 is one convention chosen inside that band, not a measured value. 12/12
would have been 12/12 at 0.40, or at 0.70. Anyone reading "threshold = 0.62" as
calibrated precision is reading something the data does not contain.

### Refused here

| Metric | Why |
|---|---|
| `roc_auc` | Perfectly separable ⇒ exactly 1.0 by construction. Adds nothing the separation margin lacks while reading as a strong result. **This repo has already shipped one metric returning a confident 1.0 with no discrimination.** |
| `loo_accuracy_confidence_interval` | ±25 points on 12 folds, and the folds are not draws from a population — they are hand-assembled members, most of whose negatives share an author with the positives. |
| `significance_test` | Tests the null that 4 winners and 8 non-winners were drawn exchangeably. They were not drawn at all. The assumption is known false. |
| `calibration_curve` | 12 points over any usable number of bins leaves 1–2 per bin, where observed frequency can only be 0, 0.5 or 1. |
| `per_subgroup_loo_accuracy` | ≤3 folds per group; accuracy can only be 0, ⅓, ⅔ or 1. |

---

## 5. Subgroup fairness (`backtest/fairness.py`)

This matters more here than anywhere, because the product's stated purpose is finding
founders others cannot see — and the system has already shipped exactly this bug once, a
transliterated name voiding an entire cohort's evidence, caught only because somebody
measured it.

### The central difficulty, stated first

Equal opportunity, FPR parity and accuracy parity are all defined over **outcomes**, and
outcomes exist only for the twelve labelled members. **Not one is international. None
carries a provenance flag.** So every outcome-based fairness metric refuses, always for
the same reason: *the group whose fairness we most need to check has zero labelled
members.* That is the finding. A number computed on three unlabelled companies instead
would be the next entry in this repo's list of things that looked implemented and
measured nothing.

### What was measured instead

**(a) Descriptive levels** — n always attached, no interval, no test. Population 27.

| Axis | Group | n | mean μ | Gap |
|---|---|---|---|---|
| International (Type 6) | international | 3 | 0.4842 | **+0.0149** |
| | rest of corpus | 24 | 0.4694 | |
| Sparse vs rich evidence | sparse | 12 | 0.4404 | **−0.0552** |
| | rich | 15 | 0.4956 | |
| Provenance-flagged | flagged | 3 | 0.4842 | **+0.0149** |
| | unflagged | 24 | 0.4694 | |

**Read: no group is disadvantaged by these numbers, and the numbers are too weak to
establish that.** Three further findings qualify them:

**Collinearity.** In this corpus *every* provenance-flagged company is international and
vice versa. Rows 1 and 3 are the **same comparison reported twice** and do not corroborate
each other. Separating the axes needs a flagged company that is not international, or an
international company with unflagged evidence. The corpus contains neither.

**Sign instability.** Re-run excluding the backtest cohort — which was assembled at the
extremes on purpose — and **all three gaps reverse sign**:

| Axis | Full corpus | Excluding cohort | Sign held? |
|---|---|---|---|
| International | +0.0149 | −0.0097 | **no** |
| Sparse vs rich | −0.0552 | **+0.0382** | **no** |
| Provenance-flagged | +0.0149 | −0.0097 | **no** |

A gap that changes direction under a defensible change of population **has not measured a
direction.** The apparent sparse-evidence penalty is cohort composition, not evidence
density. No disadvantage claim rests on any of these.

**`date_inferred` is UNTESTED, not clean.** No event anywhere in the corpus carries it.
The axis refuses by name, so a reader learns it was never exercised rather than assuming
it passed.

### (b) The counterfactual that needs no sample size

The strongest fairness instrument available at n=3, and the one that would have caught the
original bug. Take a flagged founder's real events, strip the provenance flags, re-derive
the green flags, compare the reading the filter receives. The **only** difference is the
flag, so any difference is caused by the flag. This is a paired counterfactual, not a
group comparison — it is valid on one company and makes no claim about a population.

| Company | Flags | y with | y without | Δ |
|---|---|---|---|---|
| Zaryad Compute | transliterated_name, non_english_source | 0.5741 | 0.5741 | **0.0** |
| Xiliu Inference | transliterated_name, non_english_source | 0.4815 | 0.4815 | **0.0** |
| Tantu Systems | transliterated_name, non_english_source | 0.5741 | 0.5741 | **0.0** |

**Verdict: no provenance flag changes the observation the filter receives.** Exactly zero,
not approximately. The Type 6 guarantee holds causally.

`test_flag_ablation_catches_the_bug_it_exists_to_catch` reintroduces the original blanket
filter and asserts the check fires — without it, a clean result is indistinguishable from
a check that fires for nobody.

> **Note on direction.** The ablation keys on the reading being **changed**, not
> **lowered**. Discarding flagged evidence leaves the filter with nothing and returns the
> uninformative prior of 0.5 — which is *higher* than a correctly-scored weak founder's
> reading. A check watching only for a drop would have seen that founder's evidence
> destroyed and called it fine.

---

## 6. The component harness (`memory/calibration.py`)

It was a complete, typed, no-lookahead harness — threshold sensitivity, separation
margin, hit rates — and **nothing ever supplied it a label**, so every metric returned
`None` in production. A harness with no feed is indistinguishable from one that does not
work.

`backtest/component.py` now feeds it the cohort. The dependency points
`backtest → memory`, never the reverse, preserving that module's commitment not to load a
cohort of its own.

**The one shape change it needed** was real. The harness applied a single cutoff grid to
every entity, and a historical cohort has no shared calendar: Docker's window closes in
June 2014, Supabase's opens in January 2020. A shared grid scores Supabase at 2014 where
it has no history — returning the untouched prior, which is the scorer saying "I know
nothing" and is not a reading — and scores Docker at 2021, seven years past the breakout
its cutoff exists to precede, which is lookahead. `run_calibration` now accepts
**per-entity cutoffs**.

Live output, each member at its own final pre-breakout cutoff:

```
winner_hit_rate              1.0   (4 winners)
control_false_positive_rate  0.0   (8 controls)
separation_margin            0.519
threshold sensitivity        0.57 / 0.62 / 0.67 → identical (1.0, 0.0)
```

That the sensitivity sweep is flat across ±0.05 is the same finding as §4's
indeterminacy band, arrived at independently.

**Label mapping, reported not silent:** the harness's vocabulary is
winner/control/other/unknown and the cohort also has a `failure`. For a threshold metric a
failure is a negative like any other, so it maps to `control` — meaning
`controls_evaluated: 8` is 7 controls **plus** the deprioritized failure. The mapping is
emitted in the result rather than assumed.

---

## 7. Known cross-workstream issue

`intelligence/conformal.py` derives `DEFAULT_ALPHA = 0.125` from "the repo's labelled
cohort has exactly 9 members" (8 after leave-one-out ⇒ α ≥ 1/9). Adding three real
controls takes its `_cohort_labels()` set to **12**, so `required_points(0.125) = 7` is
still satisfied and nothing breaks — the layer stays calibrated and gains three points.
But **the stated derivation is now conservative rather than exact**: at n=12, 11 after
LOO exclusion, α could tighten to 1/12 ≈ 0.083.

That file belongs to another workstream and was not edited. Flagged here for its owner.

---

## 8. Summary of every refused metric

| Metric | Where | Reason |
|---|---|---|
| `roc_auc` | crossval | Separable ⇒ 1.0 by construction; no discrimination |
| `loo_accuracy_confidence_interval` | crossval | ±25 pts on 12 non-independent folds |
| `significance_test` | crossval | Exchangeability assumption known false |
| `calibration_curve` | crossval | 1–2 points per bin |
| `per_subgroup_loo_accuracy` | crossval | ≤3 folds per group |
| `equal_opportunity_difference` | fairness | 0 international members labelled |
| `false_positive_rate_parity` | fairness | 0 international members labelled |
| `accuracy_parity` | fairness | 0 international members labelled |
| `demographic_parity_difference` | fairness | Too few members for a rate to have >2 values |
| `calibration_within_groups` | fairness | No labelled outcomes per group per bin |
| `significance_of_gap` | fairness | Smaller group n=3 |
| `mean_mu[date_inferred]` | fairness | Group empty — axis untested |
| `mean_mu` for any group < 3 | fairness | Below the descriptive floor |

---

## 9. Reproducing

```bash
uv run python scripts/seed.py                  # idempotent
DATABASE_URL="postgresql://x:y@127.0.0.1:1/none" uv run pytest tests/test_backtest.py tests/test_calibration.py -q
```

```python
from datetime import datetime, timezone
from backtest import runner, crossval, fairness, component

calibration = runner.run_calibration()
crossval.leave_one_out(calibration)                        # per-fold table + refusals
fairness.subgroup_report(datetime.now(timezone.utc))       # subgroups + ablation
component.run()                                            # memory/calibration.py, fed
```
