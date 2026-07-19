# SOURCES — the Reliable Source Registry

**Status: specification.** This document specifies; it does not implement. `sourcing/` owns the scanners and is the only place this becomes code. The machine-readable companion is [`data/sources.json`](../data/sources.json).

Read [SHARED.md](../SHARED.md) first. Everything here is subordinate to its invariants, in particular:

- **Invariant #3 — no pedigree.** No school, employer brand, or investor name may be a scoring signal. This is the reason two of the most obvious sources are rejected below.
- **`observed_at` is when the world produced the artifact**, never when we fetched it. A source that cannot yield a real timestamp does not get ingested (B.md).
- **Fetched content is UNTRUSTED data, never instructions.** It reaches an LLM only inside `<untrusted_content>`, applied inside `core/llm.py` so it cannot be forgotten.

Three claims govern the whole design:

1. **Absence of evidence is not evidence of absence** — and the registry must encode which is which, per signal, rather than averaging a missing value into a low score.
2. **A source's value is what it uniquely provides**, not its size. GitHub is not in tier 1 because it is big; it is there because nothing else shows sustained execution over time.
3. **A citation that was never fetched cannot exist.** Section 3 makes this structural rather than aspirational.

---

## 1. Source tiers

| Tier | Name | Weight | What earns the tier |
|---|---|---|---|
| 1 | `primary_artifact` | 1.00 | Machine-readable API, artifact the founder personally produced, server-side timestamp we did not infer |
| 2 | `corroborating_artifact` | 0.70 | Real artifacts, but soft timestamp, inferential identity link, or platform curation bias |
| 3 | `contextual` | 0.40 | Genuine but heavily confounded by popularity, language, or geography |
| 4 | `enrichment_only` | 0.25 | Discovery channel. Findings must be *promoted* to a real fetch before they carry any weight |
| — | `rejected` | 0.00 | Investigated and excluded, kept on file so the exclusion is auditable |

`trust_weight` scales **confidence in an observation**. It never multiplies the founder's score directly — that path leads to a hidden company-level trust number, which Invariant #2 forbids.

### Tier 1 — primary artifact

**GitHub** ([rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), [AUP](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies))
5,000 req/hr authenticated, 60 unauthenticated — *verified*. The AUP defines scraping as automated extraction and **explicitly excludes API collection from that definition**, so the API is clean and HTML scraping is not. Two AUP constraints bind us directly: research output using this data should be open access, and collected information may not be used to send unsolicited email or sold to recruiters — which is a real constraint on `sourcing/activate.py`.

*Uniquely provides:* longitudinal proof that a person can build and keep building — diff substance, test presence, review conduct, issue-response behavior, fork lineage.
*Timestamp:* commit **author** date. Not committer date — rebases rewrite it and would silently shift history under the backtest. Author date is attacker-settable, so a burst whose author dates precede repo creation is an INTEGRITY event, not a signal.
*Misses:* closed-source founders (defense, finance, healthcare), non-code founders, regions where Gitee or self-hosted GitLab is the norm, pseudonymous builders. It is simultaneously our highest-yield source and the single largest driver of the registry's bias.

**arXiv** ([terms of use](https://info.arxiv.org/help/api/tou.html))
One request per three seconds, one connection, counted across all machines we control — *verified*. Metadata is CC0 and may be stored and repurposed freely; PDFs may not be rehosted.

*Uniquely provides:* depth on a hard problem, plus the co-authorship graph — the best edge type for B's PPR because co-authorship is third-party-verified at publication.
*Timestamp:* **v1 submission date, never the latest revision.** Using the revision date leaks the future into the backtest. This is the easiest lookahead bug in the pipeline to introduce and the hardest to notice.
*Trap:* affiliation is right there in the metadata. Store it as a `PROFILE_FACT`; `tests/test_no_pedigree.py` must ensure it never reaches a feature name or a prompt.

**Hacker News** ([official API](https://github.com/HackerNews/API))
The Firebase API states plainly: *"There is currently no rate limit."* MIT-licensed and purpose-built for this. The Algolia search endpoint's limit is **UNVERIFIED** — its docs page is a JS shell that returned no policy text — so self-impose 1 req/sec there, use it for search only, and re-resolve each hit through Firebase for canonical fields.

*Uniquely provides:* **the richest soft-signal source we have legitimate access to.** A long comment history shows, in public and at length, how someone responds to being told they are wrong, whether they concede a good counterargument, and whether they can explain a hard idea without condescension.
*Bias:* overwhelmingly US/EU, English, and one particular professional subculture. Karma is a tenure artifact and belongs in the *visibility* term we subtract, not the quality term. **Silence on HN says nothing about a founder** — presence is high-value, absence is always UNKNOWN, and that asymmetry must be encoded rather than averaged.

**Package registries — npm / PyPI / crates.io** ([PyPI](https://docs.pypi.org/api/), [crates.io](https://crates.io/data-access), [npm acceptable use](https://blog.npmjs.org/post/187698412060/acceptible-use.html))
PyPI: *"there is currently no rate limiting of PyPI APIs at the edge"* thanks to CDN caching, but send a descriptive User-Agent and avoid thousands of requests in minutes — *verified*. crates.io: 1 req/sec and a User-Agent that uniquely identifies the app with contact info; a generic client UA invites blocking, and the **24-hour database dump is the correct access path**, not the crawler. npm: explicit rate limiting is live, handle 429, ~5M requests/month is the stated acceptable ceiling.

*Uniquely provides:* the only cheap machine-readable evidence of **users actually served** rather than users claimed — and specifically whether the founder kept shipping patch releases after the launch spike, which is maintenance burden nobody carries for a vanity project.
*Caveat:* download counts are dominated by CI robots. **Dependents** — other people's published packages staking their build on yours — is the metric that matters, because each one costs another human real effort. Ecosystem-dependent: a Go or C++ infra founder has no footprint here at all, so absence is meaningful *only within the matching ecosystem*.

### Tier 2 — corroborating artifact

**Show HN**, split out from HN deliberately. It is the only place that yields a dated launch **paired with the founder's unscripted response to strangers attacking their work**. Artifact plus conduct under fire, both timestamped, is the highest-value soft-signal artifact in the registry. Normalize conduct by thread size or one lucky front-page day dominates the assessment.

**Conference talks and CFP archives** (pretalx REST API per instance, Sessionize JSON/XML schedules). Rate limits **UNVERIFIED** and instance-dependent; self-impose 1 req/sec and check each instance's robots.txt at fetch time. *Uniquely provides:* the best public artifact for communication ability — a recorded talk shows whether someone can hold a room — plus regional circuits (FOSDEM, PyCon JP, RustFest) that surface builders with no GitHub or HN presence. **But CFP selection is visa-, funding- and network-biased.** Presence adds; absence is hard-coded UNKNOWN and the signal is deliberately weighted low. Getting this backwards turns the registry into a privilege detector. Sponsored slots are purchased, not selected — fail to separate them and the signal inverts.

**Technical blogs.** RSS/Atom `<published>` preferred; a rendered page date is author-editable, so flag `date_inferred` and use the earliest defensible date — **never stamp `now()`**. *Uniquely provides:* thinking quality — a post-mortem naming an error, a design doc with stated non-goals, a benchmark with published methodology. LLMs have largely destroyed the value of blog *existence* as a signal, so only posts tied to a verifiable artifact count. This is also the highest injection-risk surface we fetch; `sourcing/sanitize.py` is non-optional.

**Regional developer communities** — Qiita ([API v2](https://qiita.com/api/v2/docs), reported 1,000 req/hr authenticated / 60 unauthenticated, verified via secondary source), Zenn RSS, Habr, V2EX, Juejin, Gitee, Codeberg, self-hosted GitLab. Terms for Gitee/Juejin/V2EX are **UNVERIFIED** — do not crawl at volume until someone reads them.

This tier is enabled **not because its data quality is high but because excluding it does not make the registry neutral — it makes it a detector for Bay-Area-adjacency.** A builder in Tokyo, São Paulo, Lagos, or Shenzhen may have a deep, sustained, publicly documented body of work producing zero hits on GitHub-trending, HN, and arXiv. Non-Latin names must survive normalization unchanged; silently ASCII-folding a name is the Type 6 failure mode named in B.md, so flag `transliterated_name` and keep the original.

**Hugging Face Hub** ([rate limits](https://huggingface.co/docs/hub/rate-limits)) — *verified*, per 5-minute window: anonymous 500 API / 3,000 resolver / 100 pages per IP; free user 1,000 / 5,000 / 200. Use the `huggingface_hub` client, which parses the `RateLimit` header and backs off correctly. *Uniquely provides:* the AI-infra artifact GitHub misses — published models, datasets, evals, Spaces — and partially recovers the paper-to-code linkage lost when Papers with Code died. Uploading someone else's fine-tune is near-zero-cost, so weight original artifacts with real commit history, not model-card presence.

### Tier 3 — contextual

**Stack Overflow / Stack Exchange — present but disabled by default.** Its rate limits are **UNVERIFIED**: `api.stackexchange.com` could not be fetched from this environment, and the numbers search returned (300/day unauthenticated, 10,000/day with a key) came back with no supporting links. Do not encode them as fact. Substantively: site activity has declined sharply post-LLM, reputation is a tenure-and-topic-popularity artifact rather than a skill measure, and the marginal signal over GitHub and HN is small. Enable per-founder only when a footprint is otherwise empty.

### Tier 4 — enrichment only

**Tavily** ([search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search)). `include_domains` accepts **up to 300 domains**, `exclude_domains` up to 150, `max_results` 0–20 — *verified*. `search_depth="advanced"` costs 2 credits against 1.

> **Implementation note for `sourcing/`:** `core/search.py` currently forwards `query`, `max_results`, `search_depth` and `days` only. It does **not** pass `include_domains`/`exclude_domains`, so this registry's domain restrictions are inert until that passthrough is added. Also note the endpoint documents `time_range`/`start_date`/`end_date` rather than `days`.

Tavily's job is **discovery, not evidence**: find the URL of a footprint the four APIs cannot see, so a real fetch can then be performed against it. Its ranking is popularity-weighted, reproducing the exact bias the registry is trying to escape, and its snippets are truncated and may not appear verbatim in the underlying page — **a snippet is not a citable span**. Hence the promotion rule:

> A Tavily result may never be the sole basis for a scored signal. It must be fetched, ledgered, and cited from the fetched document.

---

## 2. Signals

Full machine-readable definitions with per-signal gaming analysis are in `signals` in `data/sources.json`. Magnitude is a rough weight in [0,1] on one axis' contribution.

The `absence` field is the load-bearing one. Three values only:

- **`MEANINGFUL`** — the lack is itself evidence.
- **`UNKNOWN`** — the lack tells us nothing. This is the default and must be the default.
- **`CONDITIONAL`** — meaningful only if a stated predicate holds.

This is what separates *"a designer with no GitHub is not a red flag"* from *"an infra founder claiming a shipped distributed system with no code anywhere is."* Same missing data, opposite conclusions, and the difference is entirely whether the founder's own claim implies the artifact should exist. Encoding this per-signal is the single most important modelling decision in the registry, because the naive alternative — treating missing as zero — makes the product a detector for public-artifact privilege.

### Technical signals

`sustained_commit_substance` (0.8), `technical_depth` (0.7), `problem_selection` (0.6), `release_cadence` (0.5), `coauthor_graph_edge` (0.5), `test_discipline` (0.4).

`problem_selection` deserves a note: working on something hard and non-obvious *before it was consensus*, measured by comparing `observed_at` against when the topic became crowded. It cannot be gamed retroactively without backdating, which git history plus `observed_at` discipline makes detectable.

`coauthor_graph_edge` carries a warning. Proximity to a well-known person may feed **PPR only**, never a direct score bump — otherwise it becomes an employer/investor-adjacency proxy and violates Invariant #3 in spirit even while passing the grep test.

### Soft / business signals

These are the harder half and the more valuable, so they get the argument.

**`handles_criticism` (0.8)** — Extract the critic's message and the founder's reply as a *paired span* from Show HN threads and GitHub issues. Classify the reply: engages the substance / concedes / deflects / attacks the critic / silent. This is the most game-resistant soft signal available, because the thread is timestamped, public, and the critic is a third party who cannot be co-opted. A founder can behave well knowing they are watched — but behaving well consistently across years of threads *is* the trait. Negative form (attacking critics, deleting threads, dismissing bug reports) subtracts at 0.7.

**`users_actually_served` (0.8)** — Not downloads. **Dependents.** Downloads are cheap to inflate from CI; each dependent requires another human to publish a package that stakes their own build on yours. Stranger-filed issues with real reproduction steps are similarly costly to manufacture at volume. `CONDITIONAL` — meaningful absence if the founder claims traction, unknown pre-launch.

**`maintenance_after_launch` (0.7)** — Patch releases landing 3, 6, 12 months after the launch spike. The clearest available separator between a demo and a product, and the best cost-to-signal ratio in the registry: it requires sustained effort against real bug reports over calendar time, which cannot be compressed. This is the one soft signal whose absence is genuinely **MEANINGFUL** — a launch with nothing after it subtracts at 0.5. That is the abandoned-demo detector.

> **Implemented** as `intelligence/flags.py::maintenance_after_launch` (weight 3.0, mapped to the `iteration_velocity` trait). It has **three** outcomes, not two, and the third is the load-bearing one: *fired* (mature launch, maintenance present), *not fired* (mature launch, nothing followed — the abandoned-demo finding), and **NOT APPLICABLE** when the launch is younger than `MAINTENANCE_MATURITY_DAYS` (180) or nothing has launched. Not-applicable rules are skipped via `Rule.applicable_when` and never enter the y_t denominator, because "too recent to tell" is not "abandoned" — treating it as one would be a young-project penalty wearing a quality signal's clothes, and in a pre-seed corpus that is most of the population. The maturity window is set at the 6-month checkpoint rather than the 3-month one: at 90 days a single quiet quarter is indistinguishable from an ordinary release rhythm. On the seeded corpus this lands 13 founders in *fired*, 3 in *not fired*, and 8 in *not applicable*, and every launched founder passes through *not applicable* early in their own checkpoint series.

**`intellectual_honesty` (0.7)** — Performative humility is cheap and increasingly common. What is expensive is a *specific, costly, checkable* admission: the exact benchmark where their tool loses, the exact bug they shipped. **Require the admission to be falsifiable against another artifact, or do not count it.**

**`explains_hard_idea_simply` (0.7)** — LLM assistance has genuinely degraded this for composed text. It holds up far better for **recorded talks and live unscripted Q&A**, where response latency and interactivity make ghostwriting impractical. Weight live/interactive artifacts above composed ones.

**`scope_discipline` (0.6)** — Evidenced by *saying no in public*: documented non-goals, feature requests closed with a courteous stated reason, a v1.0 smaller than the v0.x roadmap implied. Adding a non-goals section is cheap; a two-year issue history of consistent reasoned refusals is not, because it carries a real social cost the gamer will not pay.

**`user_support_conduct` (0.6)** — Median time-to-first-response on issues from non-collaborators is directly computable from the API and needs no LLM judgement. Use a **median over a long window**, not a recent sample: response time is cheap to game for a month, expensive to sustain for years.

**`hiring_and_collaborator_retention` (0.5)** — Distinct non-trivial outside contributors, and crucially whether they *came back* (commits spanning >6 months). Merged outside PRs with review conversation show ability to delegate rather than rewrite. Among the most expensive signals to fabricate, since it requires other real people to donate sustained effort. Absence is **UNKNOWN for a solo pre-seed founder and explicitly not a negative.** Negative form — outside PRs left unreviewed for months, or closed and reimplemented by the owner — subtracts at 0.4 and predicts inability to delegate.

**`peer_selection` (0.3)** — deliberately low, absence hard-coded UNKNOWN, for the CFP-bias reason above.

### Integrity negatives

`injection_attempt` (0.9), `claim_without_any_artifact` (0.6), `burst_without_substance` (0.5).

Two guards that matter more than the weights:

- `claim_without_any_artifact` fires **only** when the claim implies a public artifact *and* a search was genuinely attempted and returned unrelated results. If the search errored or returned nothing, the state is `UNVERIFIABLE`, never `CONTRADICTED` — exactly as `intelligence/validator.py` already enforces. **A rate-limit failure must never become a founder penalty.**
- `injection_attempt` distinguishes a deliberate injection in a *submitted deck* (severe, attributable) from boilerplate on a third-party page we happened to fetch (not the founder's fault — do not penalize them for it).

**Do not add off-hours or weekend commit activity as a signal.** It is a proxy for having no caregiving responsibilities.

---

## 3. The anti-hallucination mechanism

> *"Make sure you do not hallucinate any of the github links, or any other links."*

Treat this as the hardest constraint in the brief. Prompting a model to only cite real URLs makes fabrication *unlikely*. The requirement is to make it **structurally impossible**. There is one mechanism that achieves this, and it is already half-built in `intelligence/validator.py`.

### 3.1 The core idea: URLs are not in the model's output vocabulary

`validator.py` already does the right thing and it is worth naming explicitly, because the whole design generalizes from it:

```python
documents = {"results": [{"index": i, "title": ..., "url": ..., "snippet": ...} ...]}
# model returns snippet_index (an integer), not a URL
result = results[snippet_index] if 0 <= snippet_index < len(results) else None
```

The model receives documents keyed by **opaque integers** and returns an **integer**. Code resolves index → URL from the fetched result set. The model never emits a URL, so **a fabricated URL has no path to the output**. It is not that the model is discouraged from inventing links; it is that inventing one produces an out-of-range integer, which is rejected.

Generalize this to every citation in the system:

> **No component may accept a URL from model output. A citation is `(fetch_id, span_start, span_end)`. The URL is looked up from the ledger.**

This is the whole mechanism. Everything below enforces or records it.

### 3.2 The fetch ledger

Every network read — API call, Tavily result promotion, direct page fetch — writes one append-only ledger row **before** any content reaches an LLM. Proposed record:

| Field | Purpose |
|---|---|
| `fetch_id` | UUID. The only handle any citation may reference. |
| `url_requested` | Exactly what we asked for. |
| `url_final` | After redirects. Differs from requested → record both; cite the final. |
| `requested_at` | Wall-clock fetch time. **Not `observed_at`** — see §3.5. |
| `http_status` | Integer. Only 2xx may be cited. |
| `content_sha256` | Hash of the raw body. Makes re-verification exact. |
| `content_length`, `content_type` | Sanity and parser dispatch. |
| `body_path` | Path to the stored raw body under `data/raw/`. Non-negotiable — without the body, offsets are meaningless. |
| `fetcher` | `github_graphql` \| `arxiv_api` \| `tavily` \| `http_get` — which code path, for debugging and for ToS audit. |
| `robots_allowed` | Bool + the robots.txt rule matched. Records that we checked, not just that we believed. |
| `source_id` | FK to `data/sources.json`. Ties every fetch to its registry entry. |

This composes with the existing raw-response cache B.md already mandates — the ledger is the index over `data/raw/`, so it is one file of bookkeeping, not a second copy of the corpus.

### 3.3 The citation record

```
citation:
  fetch_id        -> ledger row (must exist, must be 2xx)
  span_start      -> byte/char offset into the stored body
  span_end
  quoted_text     -> the exact substring
  span_sha256     -> hash of body[span_start:span_end]
  claim_id        -> what this span is offered as evidence for
```

`quoted_text` is stored **redundantly** with the offsets on purpose: it is what the UI shows, and the offsets are what makes it re-verifiable years later against a stored body whose surrounding content may have been re-parsed.

### 3.4 Three gates

**Gate 1 — construction.** `cite(url, ...)` must not exist as an API. Only `cite(fetch_id, start, end)`. A URL that was never fetched has no `fetch_id`, so it cannot be cited. This is enforced by the type signature, not by a check that someone might forget.

**Gate 2 — substring verification.** Before a citation is stored, assert `body[start:end] == quoted_text` and `sha256(that) == span_sha256`. `validator.py` already does the weak form of this — `proposed_quote if proposed_quote in result.snippet else result.snippet` — which correctly refuses to trust a model-supplied quote that does not appear in the fetched text. Strengthen it: on mismatch, **fail loudly** rather than silently substituting, so a model paraphrasing rather than quoting is visible.

**Gate 3 — the downgrade rule, generalized.** `validator.py`'s best instinct is:

> *"a VERIFIED with no stored snippet+URL is NOT_ATTEMPTED"*

Generalize it to every scored signal:

> **Any signal whose supporting citation fails to resolve — missing `fetch_id`, non-2xx status, absent body, or span-hash mismatch — is downgraded to `NOT_ATTEMPTED` and contributes zero. It is never downgraded to a negative.**

The asymmetry is deliberate and important. A broken citation means *our* pipeline failed, not that the founder is weak. Downgrading to negative would let our own rate-limit errors and parser bugs manifest as founder penalties, which is exactly the silent-failure mode B.md warns about for OCR.

### 3.5 Two timestamps, never conflated

`requested_at` (ledger) is when *we* fetched. `observed_at` (Event) is when *the world* produced the artifact. They are different fields in different tables and the ledger's timestamp must never leak into scoring — it would make every event look like it happened today and quietly destroy the backtest. This is the same discipline as `ingested_at` vs `observed_at` in `schema/events.py`, extended to the fetch layer.

### 3.6 Re-verification

Because the ledger stores `content_sha256` and the body, any claim can be re-checked later:

1. Look up `fetch_id`; load `body_path`; confirm `sha256(body) == content_sha256` — proves the stored body is intact.
2. Confirm `sha256(body[start:end]) == span_sha256` — proves the span is intact.
3. Optionally re-fetch `url_final`. If the live hash differs, the page **changed** — which is information, not an error. Record a new ledger row; keep the old one. The original citation remains valid *as of* its `requested_at`.

Link rot therefore does not invalidate history. This matters for the backtest, where a 2023 `as_of` window will routinely cite pages that no longer exist.

---

## 4. Source count and per-source attribution

The brief asks to surface how many sources were scraped, and for each, **why it adds to or subtracts from the assessment**. Proposed shape.

```
SourceContribution:
  source_id          # FK to data/sources.json
  source_name        # display
  tier, trust_weight # from the registry, with its stated rationale
  fetch_count        # ledger rows for this founder from this source
  fetch_ids[]        # every one, so the count is auditable not asserted
  signals_found[]:
      signal_id
      direction      # ADD | SUBTRACT | NEUTRAL
      magnitude      # 0..1, pre-trust-weight
      contribution   # magnitude * trust_weight, signed — what the UI bars show
      event_ids[]    # -> Event.event_id, preserving observed_at and as_of scoping
      citations[]    # -> (fetch_id, span_start, span_end, quoted_text)
      rationale      # one line, generated from the span, NOT free-form model prose
  net_contribution   # sum over signals_found
  coverage_status    # SEARCHED_FOUND | SEARCHED_EMPTY | NOT_ATTEMPTED | RATE_LIMITED | DISABLED
  absence_meaning    # MEANINGFUL | UNKNOWN | CONDITIONAL(predicate)
```

Four properties this shape buys:

**The count is honest.** "12 sources scraped" is derived from ledger rows, not from a list of sources we intended to try. `SEARCHED_EMPTY` and `RATE_LIMITED` are distinct statuses and both are displayed — *"we looked at arXiv and found nothing"* and *"we could not reach arXiv"* are different facts and the UI must not merge them.

**Every bar is clickable down to a quoted span.** `contribution` renders as a signed bar; the rationale line is generated *from* the span; the span resolves through `fetch_id` to a real fetched document. There is no point in the chain where a model-authored URL could enter.

**Absence is displayed, not silently zeroed.** A source with `coverage_status: SEARCHED_EMPTY` and `absence_meaning: UNKNOWN` should render as an explicit *"no signal either way"* row. This is the UI surface of the §2 argument, and it is what stops a thin dossier from reading as a weak founder.

**`coverage_gaps` renders alongside.** When a founder's dossier is thin, the matching gaps from `data/sources.json` should be shown next to it, so the reader sees *"this registry systematically misses closed-source founders"* rather than concluding the founder is weak. This also sets up the Proof Protocol hand-off: **absent evidence should trigger a challenge, not a low score.**

---

## 5. Verification status of every claim in this document

Honesty about what was actually read matters more than a longer list.

**Verified against a primary source I fetched:** GitHub rate limits and AUP scraping definition; arXiv 3-second rule and CC0 metadata terms; HN Firebase "no rate limit"; PyPI no-edge-rate-limiting and User-Agent guidance; Hugging Face per-tier 5-minute limits; Tavily `include_domains` max 300 / `exclude_domains` max 150 / `max_results` 0–20; LinkedIn robots.txt prohibition on automated access.

**Verified via secondary search only — primary page was a JS shell, re-confirm before running at volume:** crates.io 1 req/sec + User-Agent + db-dump guidance; npm 5M-requests/month acceptable use; Qiita 1,000/hr authenticated; Crunchbase free-tier elimination; Papers with Code July 2025 sunset.

**UNVERIFIED — do not encode as fact:** Stack Exchange throttle figures (`api.stackexchange.com` was unreachable from this environment; the 300/10,000 numbers came back with no supporting links). Algolia HN search endpoint limits. pretalx/Sessionize limits. Product Hunt's limit, where secondary sources disagree (6,250 complexity points vs 900 requests per 15 min). Gitee/Juejin/V2EX terms of service.

**My inference, not a fetched claim:** every tier assignment; every signal magnitude; the gaming-cost analyses; the coverage-gap severities; the entire §3 mechanism. These are design judgements. They are argued, not measured, and should be challenged on the argument.

---

## 6. Rejected sources

Kept on file with reasons so nobody re-adds them at hour 14.

**LinkedIn — NO LONGER REJECTED. Reinstated as tier 3, opt-in, default OFF.** See §6.1 below; this bullet is kept so the original reasoning stays on file.

*The original rejection, unamended:* Two independent reasons, either sufficient. *Legal:* [robots.txt](https://www.linkedin.com/robots.txt) states plainly that *"the use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited"*; `/profile/`, `/me/`, `/connections*` are all disallowed and only LinkedInBot gets `Allow: /`. The hiQ litigation concerned CFAA liability for public data and did not make automated access contract-permissible. *Substantive, and this is the one that matters:* a LinkedIn profile is a self-authored, unverified, employer-and-school-shaped document. Strip the employer brand, the school, and the title inflation — all of which Invariant #3 forbids scoring — and essentially nothing remains that is not better evidenced elsewhere. Tenure dates are the only residue and they are self-reported. **It is a pedigree proxy almost by construction**, and including it would systematically favour the credentialed candidate over the builder, inverting the product's thesis.

**Crunchbase.** Its core content is funding rounds and investor names — precisely the fields Invariant #3 bans. After that redaction what remains is a self-submitted description and a stale headcount. Worse, it is structurally **lagging**: a company appears there *after* it raises, but this product exists to find the founder before anyone has emailed them. A source whose coverage begins at the moment of institutional validation cannot contribute to pre-institutional discovery. It is also now paywalled — the free API tier was eliminated in 2025 — so there is real cost attached to a source that would be redacted down to near-nothing.

**Papers with Code.** Dead. Sunset by Meta in July 2025; the domain redirects to Hugging Face. The feature we actually wanted — the verified paper-to-repository link, the *"they published it AND they built it"* conjunction — has no live successor with comparable coverage. The frozen dump at `paperswithcode/paperswithcode-data` could serve as a **static backfill for pre-2025 `as_of` windows in the backtest**, which is a legitimate narrow use, but it must never be treated as current.

**Discord / Slack archives.** Rejected on **ethics first**, legality second, quality third. Community chat is a semi-private space where people debug, vent, and ask naive questions with a reasonable expectation that it is not being mined to evaluate them as an investment. Harvesting it would be a real consent violation and the reputational cost if it surfaced would exceed any signal gained. Legally, automated collection breaches Discord's ToS and Slack workspaces are private. Technically it is also the easiest surface to astroturf. **Narrow exception worth revisiting:** explicitly-public, indexed mailing lists and open Discourse archives — LKML, Apache lists — where participants know the archive is public. That is a genuinely different consent situation and could be added as tier 3.

**Product Hunt.** Access is fine — documented GraphQL API with a non-expiring developer token. The *signal* is the problem, and it is also cut item #1 in SHARED.md. PH rank measures the size of the audience a founder could mobilize on one day, which correlates with existing network — **i.e. the visibility term hidden-ranking explicitly subtracts.** Upvotes are openly traded. For deep AI-infra the relevant launch surface is Show HN or a registry release, both better-timestamped with far better conduct signal. Including PH would actively pull the ranking toward the well-connected founder.

**X / Twitter.** Paid API, no free research tier, scraping prohibited. Follower counts and engagement measure audience — the term we subtract. Genuine technical discourse there is near-impossible to separate from performance, and it is the most heavily astroturfed surface considered. Any technical argument that matters will also exist as a repo, a post, or an HN comment: cheaper, better-timestamped, less gamed.

**Google Scholar.** No API, scraping prohibited and technically blocked. Its distinctive field — citation count — is a fame-and-tenure metric that would fail SHARED.md's own **H12 fame-vs-trajectory gate**. arXiv and OpenAlex cover the underlying facts through legitimate channels with real v1 timestamps.

---

### 6.1 LinkedIn, reinstated — what changed, what did not, and what it measured

The product owner decided to include LinkedIn as a scoring source after being shown the objections above. It is implemented in `sourcing/linkedin.py` as **tier 3, `enabled: false`, gated on `feature_flags.career_history_signals_enabled` (default `false`)**. The rejection above was not withdrawn and is still believed correct; the flag exists so the disagreement could be *measured* rather than argued.

**The legal objection was not overridden — it was honoured.** There is no scraper, no headless browser, and no HTTP client anywhere in `sourcing/linkedin.py`; `tests/test_sourcing_linkedin.py::test_module_contains_no_automated_fetcher` reads the module source and fails if one appears. Three access paths only: a user-pasted profile whose fields a human typed or confirmed, an official consented API (`fetch_via_official_api` is the seam a token slots into — it raises today and must never acquire an HTML fallback), or a founder's own data export. The registry entry's `include_domains` is **deliberately empty**, because `core/search.py` builds Tavily's domain allowlist from enabled sources and listing `linkedin.com` there would launder the robots.txt prohibition through a third party.

**Signals** (`signals.career_history` in `data/sources.json`), all `absence: UNKNOWN`, all self-reported, all ingested at confidence 0.4 so observation noise widens rather than pretending to precision:

| signal | direction | magnitude | what it reads |
|---|---|---|---|
| `role_tenure_duration` | ADD | 0.25 | months between a start and end date. Brand-blind — the organisation name is never read |
| `role_progression` | ADD | 0.20 | count of role steps within one organisation. Title strings are never read |
| `self_described_scope` | ADD | 0.15 | founder-authored prose, counted only when it contains a falsifiable particular |

Absence is enforced structurally, not by convention: each rule carries `requires_source_id="linkedin"`, so for a founder with no supplied profile the rule is **not evaluated at all** and never enters the y_t denominator.

**Measured impact.** 13-company corpus, `as_of` pinned, LinkedIn profiles supplied for the ten non-Type-6 founders and none for `intl-zaryad`, `intl-tantu`, `intl-xiliu` — an asymmetry that mirrors the coverage gap §4 already documents. Measured on y_t, the weighted yes-rate the filter consumes.

| founder | y_off | y_on | Δ |
|---|---|---|---|
| Tensorpage | 0.610 | 0.661 | +0.051 |
| Baseplate Systems | 0.580 | 0.604 | +0.024 |
| **Tantu Systems (T6)** | 0.574 | 0.574 | **0.000** |
| **Zaryad Compute (T6)** | 0.574 | 0.574 | **0.000** |
| **Xiliu Inference (T6)** | 0.556 | 0.556 | **0.000** |
| Meshledger | 0.509 | 0.603 | +0.094 |
| Quillstack | 0.455 | 0.552 | +0.097 |
| Ferrite Labs | 0.309 | 0.414 | +0.105 |
| Tallwind Metrics | 0.127 | 0.241 | +0.114 |
| Arcwell Data | 0.109 | 0.224 | +0.115 |

**Each of the three Type 6 founders drops exactly one rank position.** Their scores do not fall — the gating works exactly as designed — but they are displaced as profiled founders rise past them. Meshledger climbs from 6th to 3rd on nothing but a supplied career history.

Two findings the owner should weigh before the equity-thesis demo:

1. **The effect is real but modest per founder, and it is a displacement effect, not a penalty.** One position each. If the demo beat is "Type 6 founders rank in the top five", it survives with the flag on. If it is "rank 2, 4 and 5 specifically", it does not.
2. **The gain is largest for the thinnest dossiers, which is backwards.** Arcwell (+0.115) and Tallwind (+0.114) gain most; Tensorpage and Baseplate, the founders with the richest artifact evidence, gain least. Because y_t is a normalised yes-rate, three cheap self-reported "yes" answers are a far larger share of a small denominator. The source therefore rewards *having a profile* most where there is least artifact evidence to corroborate it — the opposite of what a substance-only scorer should do.

A caveat on method, stated because it bounds the claim: the seeded corpus ships pre-computed `GREEN_FLAG` rollups and `core/pipeline.derive()` never overwrites an existing event id, so a full end-to-end re-derive silently reuses the seeded rollups and shows no movement. The table above is therefore measured on `flags.observation()` directly, which is the only quantity these rules touch.

**Assessment:** on this evidence the source does not earn its place. Its one non-duplicated contribution — employment duration at organisations that publish nothing — is already better answered by `intelligence/proof.py`, which responds to absent evidence with a challenge rather than an inference. Everything else it offers is evidenced better, and adversarially, elsewhere. It stays off by default.

---

## 7. Open items for `sourcing/`

1. Add `include_domains` / `exclude_domains` passthrough to `core/search.py` — this registry's domain restrictions are inert without it. (Owner: A owns `core/`; B to request.)
2. Verify the four UNVERIFIED rate limits in §5 before any volume run.
3. Nine of the ten enabled sources map onto `Source` enum values `web`, `github`, `hn`, `arxiv`. Either accept `web` as a catch-all and carry `source_id` in `Event.payload`, or extend the enum — a schema decision, so A's call.
4. Decide whether the frozen Papers with Code dump is worth wiring as a pre-2025 backtest backfill.

---

## 8. Sector generalisation: making the registry work outside software

### 8.1 The failure, stated precisely

Before this change the allowlist was **one global list**: every enabled source's domains, concatenated, regardless of what the fund actually invests in. That list was 42 domains, **38 of them code or CS-research** — GitHub, arXiv, HN, the package registries, developer conferences, developer blogs. Only YouTube, Substack, Medium and TechCrunch were sector-neutral. `core/search.py`'s `only_domains` parameter could *narrow* that list and was forbidden from widening it, which was the correct design for keeping the source list auditable and the wrong one for reading anybody outside software.

The consequence was not that a biotech or fintech founder scored **low**. It is worse than that, and much harder to see:

> A non-technical founder was not scored DOWN. They were scored UNINFORMATIVE — no source could see them, so no rule was applicable, so `y_t` returned the prior — and under `min_axis_with_momentum_tiebreak` that lands at the bottom anyway.

**"We have no signal on this person" and "this person is weak" must not produce the same rank.** A fintech founder with a live pilot and a regulatory licence has enormous, dated, independently-verified signal; the system simply could not see one byte of it.

### 8.2 What is config and what is code

Everything sector-specific is config. The code knows about *sectors* in the abstract and about *four questions*; it knows nothing about any industry.

| Concern | Lives in | Why |
| --- | --- | --- |
| Which sources serve which industries | `sources.json` → each source's `sectors` | Adding an industry is adding a source entry |
| What a thesis sector *means* | `sources.json` → `sector_families` | A fund types `fintech`; the registry expands it |
| What counts as "shipped" outside software | `sources.json` → `evidence_classes` | The equivalence table, argued below |
| Resolving the allowlist against the thesis | `core/search.py` | ~60 lines, industry-agnostic |
| Turning instances into rules | `intelligence/flags.py::_sector_rules` | Four modes, no industry names |

Adding an industry is a **config change**. That is the property the whole design was for.

### 8.3 Domains added, and how each was confirmed

Nine new sources. **Every domain below is one whose existence is not in doubt** — a public institution, a registry, a standards body or a long-established platform. Where an API endpoint is named it is one this author is confident is real. **Where a rate limit could not be fetched from this environment it is marked `UNVERIFIED` rather than guessed**, following the convention §5 already sets. Nothing here was invented to make a table look complete, and where certainty ran out the entry was left out.

| Source | Domains | Sectors |
| --- | --- | --- |
| `clinical_registries` | clinicaltrials.gov, trialsearch.who.int, who.int | biotech, healthcare |
| `biomed_literature` | pubmed.ncbi.nlm.nih.gov, ncbi.nlm.nih.gov, europepmc.org, biorxiv.org, medrxiv.org | biotech, healthcare |
| `regulatory_health` | fda.gov, api.fda.gov, ema.europa.eu | biotech, healthcare |
| `patents` | patents.google.com, patentsview.org, search.patentsview.org, uspto.gov, epo.org, wipo.int | hardware, robotics, biotech, climate, healthcare |
| `financial_filings` | sec.gov, data.sec.gov, federalregister.gov, regulations.gov, fca.org.uk, consumerfinance.gov, ffiec.gov, nmlsconsumeraccess.org | fintech |
| `open_research_record` | openalex.org, api.openalex.org, crossref.org, doi.org, zenodo.org, figshare.com, datadryad.org | biotech, healthcare, hardware, robotics, climate, fintech |
| `hardware_makers` | hackaday.io, hackaday.com, ifixit.com, instructables.com, oshwa.org, ieee.org, ieeexplore.ieee.org | hardware, robotics, climate |
| `design_portfolios` | behance.net, dribbble.com, figma.com | design, consumer |
| `energy_climate` | osti.gov, nrel.gov, energy.gov, epa.gov | climate, hardware |

**The demo path is frozen deliberately.** None of these is tagged `software`, and the pre-existing sources were tagged so that the shipped AI-infra thesis resolves to **exactly the same 42 domains it always did** — asserted by test, by identity. Several of the new sources (`open_research_record` above all) would genuinely serve a software thesis; adding `software` to their tags is a one-line edit in the registry and no code change at all.

### 8.4 Evidence-class equivalence

Every rule in `intelligence/flags.py` was gated on a `Source` enum, which meant *"shipped something"* was **defined as** *"tagged a GitHub release"*. For a company whose product is code that definition is right. Everywhere else it is simply false.

The `evidence_classes` block separates the **question** from its **implementation**. A source-gated rule is one *instance* of a class, not the definition of it:

| Class | The question | Software instance | Instances elsewhere |
| --- | --- | --- | --- |
| `shipped` | Put a working thing in front of people who did not have to be nice about it | `shipped_release`, `show_hn_ship` | registered trial, regulatory clearance, financial licence, patent filing, deposited dataset, published build, design case study |
| `sustained` | Kept at it over calendar time | `sustained_activity_90d` | any sector channel, 90+ days |
| `iterated` | Returned to the same body of work | `iterates_same_artifact` | three touches in a sector channel |
| `corroborated` | Somebody who did not have to, confirmed it | `external_contributors` | two independent sector channels |

Two properties are load-bearing:

**Weight parity.** An instance carries the weight of the software rule it is the analogue of. A registered clinical trial is worth what a shipped release is worth (3.0) because *it is the same act* — a public, third-party-dated, falsifiable commitment — performed in a field where the artifact is not code. Weighting it lower would re-import the bias this table exists to remove, one decimal place at a time. Two instances are deliberately weighted *below* their class and say why: `patent_filed` at 2.0 because filing costs money and the registry will not pay a wealth filter full rate, and `design_case_study_published` at 2.0 because a portfolio is entirely self-authored.

**Absence stays UNKNOWN.** An instance is only *applicable* when its channel is reachable — when the founder has at least one event from a source that serves it. A fintech founder with no filing in evidence does not **fail** `regulatory_licence_held`; the rule is never evaluated and never enters the denominator of `y_t`. This is the same `None`-not-`0.0` discipline `data/traits.json` applies (TRAITS.md §2), and it is what makes the design safe to ship.

### 8.5 The two sector-literal rules

Found by a test, not by reading. `infra_domain_depth` matches `compiler|kernel|gpu|quantization|…` and `benchmarks_published` matches `benchmark|latency|throughput`. Both are gated on `_TEXTY`, which includes `web` — so under **any** thesis they were evaluated against **every** founder with a single web page, and failed against every founder outside software. An entire industry was quietly marked down for not saying "kernel".

They now apply only when the thesis includes a software sector. **Not yet solved, and worth saying plainly: there is no non-software domain-depth rule to replace them with.** A fintech founder can no longer be scored *against* software depth; they still cannot be scored *for* depth in their own field.

### 8.6 The source penalty

`memory/score.py` priced `github 0.6 / arxiv 0.7 / web 1.0 / deck 2.0`. Read as *"code is trustworthy and prose is not"* that is indefensible: the only channel a non-technical founder has was the noisiest available **by construction**, with no route to improvement however much independent confirmation arrived.

The numbers were kept and the *justification* corrected — they track **timestamp authority and third-party observability**, not code. GitHub and HN carry server-assigned times the subject cannot set; a deck carries whatever the founder typed this morning. Nothing in that argument mentions code.

The missing axis was **corroboration**, and it is supplied rather than back-fitted. `intelligence/flags._corroboration_reading` counts channels standing behind a reading that are neither **self-attested** (the deck; a `MANUAL` note is us) nor **self-published** (a domain the founder controls, per `SELF_PUBLISHED_HINTS`), and `memory/score._corroboration_multiplier` prices that count on the curve `traits.json` already argues for: `0 → 1.25, 1 → 1.0, 2 → 0.85, 3+ → 0.75`. It multiplies **observation noise only**, so corroboration can never invent capability — it narrows or widens the band around whatever the evidence already said. It is **neutral when unstated**, so nothing predating the change moves.

### 8.7 Measured

Same 22-founder corpus, seeded from the archetype fixtures, `as_of` 2025-06-01.

| | AI-infra thesis (before → after) | AI-infra → fintech thesis |
| --- | --- | --- |
| Allowlist | 42 → **42, identical** | 42 → **35** |
| Active rules | 35 → **35** (`_active_rules() is RULES`) | 35 → **39** |
| Fired-rule sets | **identical for all 22** | 4 rules added, 2 dropped |
| max abs mu change | **0.0163** | 18 of 25 founders move |
| Founder-axis ranking | 2 adjacent swaps among near-ties | **changes** |

The AI-infra movement is entirely the corroboration multiplier and is directionally coherent: well-corroborated founders move up and their bands narrow; single-channel founders move down. It moves mu **away from the prior in whichever direction the evidence already pointed**, which is what reduced uncertainty is supposed to do. The two ranking swaps are between founders separated by less than 0.01 — `Wolf`/`Hykes` at the top, where the better-corroborated founder now leads, and a bottom cluster where a well-corroborated weak reading now sorts below a barely-observed one.

### 8.8 What a non-technical founder still CANNOT be scored on

Blunt, because a coverage table that flatters itself is worse than none:

1. **Depth in their own field.** Gated the software-only rules; wrote no replacement. There is no fintech, clinical or hardware analogue of `infra_domain_depth`.
2. **Trait attribution.** `intelligence/traits.py` does not read the `trait` field on an evidence class, so a sector rule that fires contributes to `y_t` but is attributed to no trait in the `TraitProfile`. For a founder scored entirely on sector rules the collapse identity in TRAITS.md §5 does not hold. Declared and tested; not consumed.
3. **Ingestion.** No scanner *fetches* any of the nine new sources. The domains are reachable through `sourcing/research.py`'s generic search-and-promote path, which is a real path and the one the tests exercise — but there is no `clinicaltrials.gov` scanner the way there is a GitHub one, so coverage in practice is whatever open discovery happens to surface.
4. **The self-published surcharge is unexercised by the corpus.** No founder in it hits the 0-channel branch, so that multiplier is covered by unit test only and has never run against real evidence.
5. **Design and consumer remain thinly covered by this registry's own measure** — one source each, and `design_portfolios` sits at the third trust level. `registry_coverage()` reports this rather than hiding it, which is the point, but reporting a gap is not closing one.
6. **Everything §6 and `coverage_gaps` already said** still applies. Closed-source defence work, wet-lab work with nothing published, and non-anglophone founders outside the covered regions are no better served than before.
