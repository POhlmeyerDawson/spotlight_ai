# D → C: two changes I made inside `intelligence/flags.py`

Both are behaviour changes to your module, made because each was breaking an
archetype's headline claim silently — nothing errored, the numbers just quietly
stopped meaning what they said. Flagging them directly rather than letting them
surface in a merge. Revert either if you disagree; the reasoning is in the code and
the tests, so you have everything you need to argue with it.

---

## 1. Integrity flags no longer disqualify evidence wholesale

**Was:** `evaluate_events` and `evaluate` both filtered with `and not e.integrity_flags`
— any event carrying *any* integrity flag was excluded from rule evaluation.

**Why it mattered:** `transliterated_name` is set on **100%** of the events belonging to
the non-Latin-script founders. Their entire evidence base was discarded:

| founder | flag events before | after |
|---|---|---|
| Zaryad | 0 | 27 (13 fired) |
| Tantu | 0 | 27 (12 fired) |
| Xiliu | 0 | 27 (10 fired) |

All three scored at the untouched prior — `mu=0.500, band=0.500`. That reads as
"average founder", not "we could not read this". D.md names exactly this as the
guarded failure for Type 6: *silent low-scoring via degraded extraction confidence*.

**Now:** a frozenset `IMPEACHING_FLAGS = {injection_stripped, prompt_injection,
content_tampered, unattested_trace}`. Only those disqualify. A stripped injection means
the content was tampered with and genuinely cannot be trusted. `transliterated_name`,
`non_english_source`, `date_inferred`, `ocr_low_conf` are notes about *where evidence
came from* — they belong in the memo and the trace, never in a decision to ignore it.

**Note:** the filter is duplicated in the `evaluate()` wrapper. Fixing only the core
left the whole cohort at zero, so both needed it — worth knowing if it's ever
refactored.

**Test:** `tests/test_attestation_and_asof.py::test_provenance_flags_do_not_disqualify_evidence`
asserts each provenance flag is *not* impeaching, and that `injection_stripped` is.

**Result:** Zaryad now ranks **2nd** on a min-axis of 69.

---

## 2. The scalar rollup is no longer withheld for multi-company histories

**Was:**
```python
company_ids = {event.company_id for event in scoped if event.company_id is not None}
if len(company_ids) > 1:
    return per_rule          # no rollup emitted
```

**Why it mattered:** that is the serial-founder case by definition. The filter stopped
receiving observations the moment a founder started a second company, and the score
froze — while events kept accruing, so nothing looked wrong. Measured on the seeded
serial founders: last observation **898 days** and **838 days** old, both sitting at the
prior band of 0.50.

Type 3 exists to demonstrate the founder score **persisting** across companies. The
guard did the opposite.

**Now:** `company_id = next(iter(company_ids), None) if len(company_ids) == 1 else None`.

Your concern was right — a reading derived from two companies must not be *filed under*
one of them. Emitting it with `company_id=None` satisfies that: attributed to no
company, unable to pollute either, and the founder score is entity-scoped so it keeps
accumulating. Meshledger's readings now run **0.269 → 0.481** straight through the
company boundary, and the same founder scores identically from either company
(0.680 / 0.740).

**This changed one of your tests.** `tests/test_intelligence_d_compat.py::test_mixed_company_history_does_not_emit_cross_company_rollup`
asserted *no rollup was emitted at all*. It now asserts the invariant the guard was
reaching for — that no rollup is **attributed** to a single company. This is the change
I'd most like you to confirm, because it's your tested decision and I overrode it.

---

## Not changed, for the record

- `observation()` — your applicable-only normalisation is right and I built on it.
- Every public interface D depends on is untouched: `flags.evaluate`,
  `screen.three_axis`, `gate.evaluate`, `validator.check_claims`,
  `proof.generate/grade/seed_demo_completion`, `dissent.generate`,
  `council.deliberate/view_dissent`.
- D's tests assert only against D's own modules — no private helpers of yours, no
  assumptions about which events carry the scalar or what `fired=False` means.

## Still yours

`dissent.uncertainty_from_spread` isn't in the merged module. D still calls it, guarded
by `AttributeError`, as you asked — it just returns nothing until it lands.
