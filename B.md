# B — Sourcing, Ingestion Bus & Graph

**You own 30% of the rubric on your own (Data Architecture & Intelligence). The graph is the "finds founders others can't see" half of the thesis — without it we're a deck reader.**

Owns: `/sourcing/`
Read [SHARED.md](SHARED.md) — you produce `Event` objects and nothing else.

---

## H0–1 — with everyone
- [ ] Schema lock. Your job in that conversation: make sure every source you'll scrape has an honest `observed_at`. GitHub commits → author date. HN → post time. arXiv → v1 submission date. **If a source can't give you a real timestamp, it doesn't get ingested.**
- [ ] Your 12 labels.

## H1–3 — scanners producing events

Everything writes through `bus.ingest()` (built next block). Until A's store lands at H3, dump to `data/raw/*.jsonl` and replay.

- [ ] `sourcing/scanners/hn.py` — HN Algolia API. Query AI-infra/dev-tools terms (`inference`, `vector db`, `compiler`, `agent framework`, `Show HN` + infra keywords). Pull post + author + comment threads.
- [ ] `sourcing/scanners/github.py` — GraphQL. For a login: repos, commit cadence, release history, languages, contributors, fork lineage. **Get pagination + rate-limit backoff right now** — you'll be running this against hundreds of nodes for the graph and a naive client dies at H10.
- [ ] `sourcing/scanners/arxiv.py` — cs.LG/cs.DC/cs.PL listings, authors + affiliations. Store affiliation as a *fact*, never as a *score input* (Invariant #3).
- [ ] `sourcing/scanners/web.py` — Tavily (`core/search.py`) as the **enrichment** channel, not a primary scanner. Given a resolved name/handle, sweep for footprint the three APIs miss: personal sites, non-English blogs, regional dev communities, conference talks. **This is disproportionately valuable for Type 6** — the invisible-international founder is precisely the one whose work isn't on HN.
  - Tavily results rarely carry a trustworthy publish date. If you can't extract a real one, set `observed_at` to the earliest defensible date and flag `integrity_flags: ["date_inferred"]`. **Do not silently stamp `now()`** — that quietly poisons the backtest, and it'll be invisible until H14.
- [ ] Cache every raw response to `data/raw/`. You'll re-run extraction ten times; don't re-hit the APIs and don't burn rate limit.
- [ ] Product Hunt: **skip it.** It's cut item #1. Only if you're somehow ahead at H16.

## H3–8 — the ingestion bus (this is the quality bar the judges see)

- [ ] `sourcing/bus.py` — `ingest(raw: RawSignal) -> list[Event]`. Normalize → sanitize → stamp → emit. One funnel for inbound decks and outbound scanners alike. Same schema, same path, no special cases.
- [ ] `sourcing/deck.py` — PDF OCR. **Keep slide IDs on every extracted span** — the memo cites "slide 7," and per-claim traceability is 25% of the rubric.
  - `pdfplumber` for text-layer PDFs, fall back to OCR only when a page yields nothing.
  - Low-confidence extraction → `integrity_flags: ["ocr_low_conf"]` and reduced `confidence`. **Type 6 fails silently if a bad scan just quietly scores low** — flag it so D can surface "we couldn't read this well" instead of penalizing the founder.
- [ ] `sourcing/sanitize.py` — the injection guard. Type 5's live demo beat is yours.
  - Detect: imperative-to-model phrasing ("ignore previous", "you are now", "system:"), role tokens, invisible/zero-width chars, white-on-white text, base64 blobs.
  - **Strip, don't reject.** Log an `INTEGRITY` event with the exact offending span quoted — the trace showing the caught injection *is* the demo.
  - Everything downstream wraps content in `<untrusted_content>` with an explicit data-not-instructions directive. Provide the wrapper as a function so C and D can't forget it.
  - Test with your own Type 5 deck before you trust it.
- [ ] Test: `tests/test_bus_injection.py` — 6 injection variants in, 6 INTEGRITY events out, zero instructions surviving into prompt text.

## H8–12 — the graph (the differentiator)

`sourcing/graph.py`.

```
V = people (HN ∪ GitHub ∪ arXiv), resolved through A's resolver — call it, don't roll your own
E = co-commit | fork lineage | co-authorship | same-thread reply
    each edge weighted and observed_at-stamped (edges must be as_of-filterable for the backtest)
```

- [ ] Build V by resolving every handle through `resolver.resolve()`. Ambiguous → keep both nodes, don't guess.
- [ ] Personalized PageRank seeded on known breakout founders. `networkx.pagerank(G, personalization=seeds)` is fine — don't write your own.
- [ ] The actual insight:
  ```
  hidden(v) = z(ppr_score(v)) − z(visibility(v))
  visibility = followers + stars-on-owned-repos + HN karma, log-scaled
  ```
  High proximity to greatness, low individual visibility = **the pre-signal founder nobody has emailed yet.** That's the pitch.
- [ ] `graph.hidden_ranking(as_of, k)` — must respect `as_of` on edges. The backtest replays this.
- [ ] Eyeball the top 20. If they're all famous, your visibility term is too weak or your seeds too narrow. Fix before H12.

## H12–16
- [ ] `graph.access_lift(picks)` → % of top-K with near-zero traditional visibility. **One number, and it's the closing line of the pitch.** Hand it to D early.
- [ ] `sourcing/burst.py` (with C) — commit-burst signature for Type 5. Real fast builders and gamers both spike; separate them on **substance**: diff entropy, test presence, file diversity, whether commits touch real logic or reshuffle whitespace. Burst alone must never be the flag — that's a false positive on a legit hacker and it'll get called out in Q&A.
- [ ] `sourcing/activate.py` — outreach draft from the trace ("we noticed your work on X"). ~40 lines. Cut item #3; if you're behind, D just mentions it exists.

## H16–18 integration · H18–21 real demo founders live · H21 freeze

## H18–21 specifically
Run the real pipeline on actual founders from your scanners — not fixtures. This is where you find that a live HN profile has a field your parser assumed away. Budget the whole block for it.

---

## Definition of done
`bus.ingest()` turns any raw signal into schema-valid, injection-free, timestamped events; `graph.hidden_ranking(as_of)` returns a ranked list where the top names are genuinely non-obvious; `access_lift` is a real number computed from real picks.

## Watch out for
- Building the graph before the bus works. The graph is fed by the bus.
- Rate limits at H10 with no backoff. Do it in H1–3 when it's cheap.
- Silently dropping non-Latin names during normalization — that's the Type 6 failure mode, and it's the beat we close the pitch on.
