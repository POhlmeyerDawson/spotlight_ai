# C — Screening, Proof Protocol, Validator & Dissent

**You own the reasoning layer and 25% of the rubric outright (Intelligent Analysis & Trust). The Proof Protocol is the centerpiece demo beat — it's the structural answer to "what about founders with no signal," which is the question every other team will hand-wave.**

Owns: `/intelligence/`
Read [SHARED.md](SHARED.md) — Invariant #3 (no pedigree) is enforced by *your* test.

---

## H0–1 — with everyone
- [ ] Schema lock. Your ask in that conversation: `GREEN_FLAG`, `VALIDATION_RESULT`, and the three `PROOF_*` kinds carry the payload shapes you need.
- [ ] Your 12 labels.

## H1–3 — the green-flag question set

- [ ] `intelligence/flags.py` — 30–50 rules, each an interpretable YES/NO with a weight. Trajectory-tuned:
  - shipped something users touch, unprompted, more than once
  - iteration velocity *on the same artifact* (revisiting > starting new)
  - handles ambiguity: scoped a vague problem into a concrete one
  - technical depth relative to the problem, not to a résumé
  - evidence of learning from a failure (rewrite, postmortem, reverted approach)
- [ ] `intelligence/banned.py` — the banned list: school, employer brand, investor names, "top-tier", "ex-", "prestigious", degree names, YC/a16z/etc.
- [ ] `tests/test_no_pedigree.py` — greps every prompt string and feature name in the repo against the banned list. **You own this test for the whole team; it runs in CI and it's a hard fail.** This is the Type 6 guarantee, by construction rather than by intention.
- [ ] Sketch gate logic on paper, share with A (its output becomes A's `y_t`).

## H3–8 — screening + validator

- [ ] `intelligence/screen.py` — `three_axis(company_id, as_of) -> ScreeningResult`.
  - **Founder | Market | Idea-vs-Market. Never averaged into one number, anywhere, including the UI.** A great founder on a dead market is a different decision than a mediocre founder on a great one, and averaging destroys exactly that distinction.
  - Each axis: `{score, trend, confidence, evidence_event_ids}`. No score without receipts.
  - Founder axis reads A's `score.founder()`. Don't re-derive it.
- [ ] `intelligence/validator.py` — per-claim, **four states, and the fourth matters**:
  - `VERIFIED` — independent source agrees
  - `CONTRADICTED` — independent source disagrees

  **"Independent source" = `core/search.py` (Tavily).** For each extracted deck claim, search for corroboration, then have the LLM judge agreement against the returned snippets. Two rules that make this defensible rather than decorative:
  - The retrieved snippet gets stored as the `evidence_span` on the `VALIDATION_RESULT` event, with its URL. D's trace drills into it. A verdict without a quoted snippet is a `NOT_ATTEMPTED`, not a `VERIFIED`.
  - Search results are **untrusted content** — a founder can plant a page. Route them through the same `<untrusted_content>` wrapper, and weight a self-published source (their own blog/LinkedIn) below an independent one.
  - Empty search results mean `UNVERIFIABLE`, never `CONTRADICTED`. Absence of evidence is not evidence of absence, and conflating them is how we'd wrongly torch a Type 6 founder whose footprint isn't in English.
  - `UNVERIFIABLE` — checked, nothing exists to check against
  - `NOT_ATTEMPTED` — we didn't look (be honest; judges respect this)
  - Per-claim Trust Score. **Contradiction reprices the claim, not the deal** — a false ARR number kills the revenue claim and widens uncertainty; it doesn't zero the founder.
  - Timestamps decide fraud-shaped vs time-shaped: "$40K ARR" stated in March against a "pre-revenue" post from January is *growth*, not a lie. Compare `observed_at`, always. **This nuance is the Type 4 demo beat — get it right or the beat lands as a bug.**
- [ ] Emit green-flag reads as observations for A's filter. Agree the exact payload with A in person, not over chat.

## H8–12 — PROOF PROTOCOL (the centerpiece — protect this block)

`intelligence/proof.py`.

- [ ] `generate(company_id) -> Challenge`. LLM reads the deck's central technical claim → founder-specific micro-challenge, 60–90 min of work. Deliberately includes:
  - **one ambiguous requirement** — do they ask, or do they assume and state the assumption?
  - **one planted bad constraint** — something subtly wrong. Do they push back, or comply?
  The planted constraint is the sharpest signal in the whole system. Nobody else will have it.
- [ ] `grade(challenge_id, artifact, trace) -> list[Event]`. Two components:
  - artifact quality (does it work, is it sound, did they handle the ambiguity)
  - **behavioral trace: iteration count, time-to-first-commit, latency profile, whether they challenged the bad constraint.** Behavior is harder to fake than output.
- [ ] Write results back as `PROOF_ARTIFACT` / `PROOF_BEHAVIOR` events → A's filter takes them as low-noise observations → founder re-enters the gate with a **visibly** moved score. That re-entry is the demo.
- [ ] `intelligence/gate.py` — `PROCEED` (evidence sufficient) | `PROOF_PROTOCOL` (thin) | `NO_CALL`.
  - Absence classifier: **signal-absent-because-irrelevant vs signal-absent-and-suspicious.** A designer with no GitHub is not a red flag; an infra founder claiming a distributed system with no code anywhere is. Get this wrong and we punish exactly the founders the thesis exists to find.
- [ ] Confidence intervals stay **wide and displayed**. Never let the Proof Protocol result masquerade as full diligence.
- [ ] **Hackathon reality:** generator + grader are real. The completion is pre-run — teammate does one challenge during build hours, or a synthetic artifact + trace. **Say so on stage.** Honesty scores better than a discovered fake.

## H12–16 — Dissent Engine

`intelligence/dissent.py`.
- [ ] Second agent, same evidence graph, inverted objective. Produces a **full anti-memo**: bear case, weakest evidence, and — the required output — **the single load-bearing claim that kills the thesis if false.** Named explicitly, not hedged.
- [ ] The recommendation stays locked until dissent is opened. Enforce it in the API response shape (`recommendation: null` until `dissent_viewed`), not in the frontend — D shouldn't be able to accidentally bypass it, and neither should we during a live demo.
- [ ] Bull/bear spread on any axis widens uncertainty. Wide spread → pushes toward no-call. Hand the spread number to D; it's a UI element.
- [ ] Contradiction repricing on the Type 4 seed — verify end-to-end with D's fixture.

## H16–18 integration · H18–21 no-call thresholds + Type 5 seed with B · H21 freeze

## H18–21 specifically
- [ ] Conformal no-call thresholding: calibrate on held-out labels so "no call" fires at a defensible rate, not an arbitrary cutoff. Being able to say *why* the threshold is where it is beats any accuracy number in Q&A.
- [ ] Anti-gaming with B: ten trivial ships ≠ one real one. Quality-weight milestones (Type 3 guard).

---

## Definition of done
Three axes with receipts and no averaging; four validator states with per-claim trust; a Proof Protocol that generates a real challenge, grades a real artifact, and visibly moves the score; a dissent agent that names the load-bearing claim and gates the recommendation.

## Watch out for
- Letting the Proof Protocol slip past H12. It's the beat that wins this — everything else is a nicer version of what other teams built.
- Averaging the three axes "just for the ranked list." Rank on a lexicographic or explicit policy, never a mean.
- A dissent agent that writes a polite balanced take. Prompt it adversarially: its job is to kill the deal, and a limp anti-memo makes the whole feature read as theater.
