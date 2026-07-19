"""Subgroup fairness measurement for the founder score. Owner: D, with A + C.

WHY THIS MATTERS MORE HERE THAN ANYWHERE
----------------------------------------
The product's stated purpose is finding founders other people cannot see. A score that
systematically reads lower for founders whose evidence arrives transliterated, in a
non-English source, or simply thin, would defeat that purpose while looking like it was
working. The system has already shipped exactly that bug once — a transliterated name
caused an entire cohort's evidence to be discarded — and the only reason it was caught
is that somebody measured it. This module is that measurement, kept.

THE CENTRAL DIFFICULTY, STATED UP FRONT
---------------------------------------
Standard fairness metrics — equal opportunity, false-positive-rate parity, accuracy
parity — are all defined over OUTCOMES, and outcomes exist only for the twelve labelled
backtest members. Not one of them is international, none carries a provenance flag, and
none has sparse evidence by the corpus median. So every outcome-based fairness metric in
this file REFUSES, and it refuses for the same reason each time: the group whose fairness
we most need to check has zero labelled members. That is the finding. Papering over it
with a number computed on three unlabelled companies would be the fourth entry in this
repo's list of things that looked implemented and measured nothing.

WHAT CAN HONESTLY BE MEASURED INSTEAD
-------------------------------------
Two things, and they are different in kind:

1. DESCRIPTIVE SCORE LEVELS per subgroup, with n always attached and never an interval.
   These say what the corpus currently looks like. They are not evidence of bias or of
   its absence, and the report says so in the same breath as the number.

2. A COUNTERFACTUAL FLAG ABLATION, which needs no sample size at all. Take a real
   flagged founder's real events, strip the provenance flags, re-derive the flags and
   re-read the observation. If the reading moves, carrying a provenance flag costs you
   score — and that is a causal statement about the code, established on one founder,
   not a correlation fished out of three. This is the strongest fairness instrument
   available at this n, and it is the one that would have caught the original bug.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

# Provenance markers that describe HOW evidence reached us, never what it is worth.
# `date_inferred` is included deliberately even though the corpus currently has none —
# the empty group produces a refusal that names it, which is how a reader learns the
# axis is untested rather than assuming it was checked.
PROVENANCE_FLAGS = ("transliterated_name", "non_english_source", "date_inferred")

# The international archetype. Its companies are the reason the Type 6 guarantee exists.
INTERNATIONAL_ARCHETYPE = 6

# Below this, a group mean is not reported at all — not even descriptively. Three is not
# a defensible sample; it is the smallest number for which a mean is arithmetically
# defined, and that is precisely the distinction this constant exists to enforce.
MIN_DESCRIPTIVE = 3

# Outcome-based rates need both classes present in the group. This is a floor on the
# smaller class, and nothing in this corpus comes close to meeting it.
MIN_PER_CLASS_FOR_RATES = 5


class Refused(dict):
    """A fairness metric deliberately not computed, with the reason a reader can check."""

    def __init__(self, metric: str, group: str | None, reason: str) -> None:
        super().__init__(refused=True, metric=metric, group=group, reason=reason, value=None)


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------


def _population(as_of: datetime) -> list[dict]:
    """Every scoreable company in the store, with the attributes the subgroups split on."""
    from api.routers.deps import founder_entity_ids
    from memory import score, store

    rows: list[dict] = []
    try:
        companies = store.get_store().companies()
    except Exception as exc:  # noqa: BLE001 - no store is an empty population, never a crash
        log.info("fairness: store unavailable (%s)", exc)
        return []

    for company in companies:
        try:
            events = store.events(company_id=company.company_id, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 - one unreadable company must not kill the rest
            log.info("fairness: events unavailable for %s (%s)", company.company_id, exc)
            continue
        # Derived readings are the system's own output, not evidence it received. Counting
        # them as "evidence" would make every company look rich and collapse the sparse
        # vs rich split onto how much the pipeline had already run.
        raw = [e for e in events if str(e.kind) not in {"green_flag", "integrity"}]
        if not raw:
            continue
        flags: set[str] = set()
        for event in events:
            flags |= set(event.integrity_flags or [])
        entity_ids = founder_entity_ids(company.company_id)
        if not entity_ids:
            continue
        try:
            founder = score.founder(entity_ids[0], as_of)
        except Exception as exc:  # noqa: BLE001
            log.info("fairness: %s unscoreable (%s)", company.company_id, exc)
            continue
        rows.append(
            {
                "name": getattr(company, "name", None),
                "archetype": getattr(company, "archetype", None),
                "raw_events": len(raw),
                "provenance_flags": sorted(flags & set(PROVENANCE_FLAGS)),
                "mu": founder.mu,
                "band": founder.band,
                "is_cohort": getattr(company, "name", None) in _cohort_names(),
            }
        )
    return rows


def _cohort_names() -> frozenset[str]:
    """Backtest cohort company names.

    They matter to a fairness split because they are not ordinary corpus members: they
    were assembled at the extremes on purpose — four companies chosen because they broke
    out, against controls chosen or composed because they did not. Any subgroup that
    happens to contain more of them inherits that spread, and the difference gets read as
    an effect of the subgroup. Every comparison is therefore reported twice, once with
    them and once without.
    """
    try:
        from backtest import collect

        return frozenset(
            str(member.get("name")) for member in collect.load_cohort()["members"] if member.get("name")
        )
    except Exception as exc:  # noqa: BLE001 - no cohort file just means no exclusion set
        log.info("fairness: cohort names unavailable (%s)", exc)
        return frozenset()


# ---------------------------------------------------------------------------
# Descriptive subgroup levels
# ---------------------------------------------------------------------------


def _describe(rows: Sequence[dict], label: str) -> dict:
    if len(rows) < MIN_DESCRIPTIVE:
        return dict(
            Refused(
                "mean_mu",
                label,
                f"group has {len(rows)} member(s); below the {MIN_DESCRIPTIVE}-member floor "
                f"at which a mean is reported even descriptively",
            )
        )
    values = sorted(row["mu"] for row in rows)
    return {
        "group": label,
        "n": len(values),
        "mean_mu": sum(values) / len(values),
        "min_mu": values[0],
        "max_mu": values[-1],
        "members": [row["name"] for row in rows],
        # No standard error, no interval, no test. See `refused` on the report.
        "interpretation": "descriptive only; not evidence of bias or of its absence",
    }


def _split(rows: Sequence[dict], predicate: Callable[[dict], bool]) -> tuple[list, list]:
    inside = [row for row in rows if predicate(row)]
    return inside, [row for row in rows if row not in inside]


def _comparison(rows: Sequence[dict], predicate, name: str, other: str, axis: str) -> dict:
    inside, outside = _split(rows, predicate)
    left, right = _describe(inside, name), _describe(outside, other)
    gap = None
    if not left.get("refused") and not right.get("refused"):
        gap = left["mean_mu"] - right["mean_mu"]
    return {
        "axis": axis,
        name: left,
        other: right,
        "mean_mu_gap": gap,
        "gap_reading": _read_gap(gap, left, right, name, other),
        "significance": dict(
            Refused(
                "significance_of_gap",
                axis,
                f"the smaller group has {min(len(inside), len(outside))} member(s). No test "
                f"of a difference in means is interpretable here, and reporting one would "
                f"convert a description of 3 companies into a claim about a population.",
            )
        ),
    }


def _read_gap(gap: float | None, left: dict, right: dict, name: str, other: str) -> str:
    if gap is None:
        return (
            f"not computed: {name if left.get('refused') else other} is below the "
            f"{MIN_DESCRIPTIVE}-member floor"
        )
    direction = "lower" if gap < 0 else "higher"
    return (
        f"{name} scores {abs(gap):.3f} {direction} on average than {other} "
        f"(n={left['n']} vs n={right['n']}). At these group sizes a gap of this size is "
        f"consistent with either a real effect or with which particular companies happen "
        f"to be in the corpus, and this report does not distinguish them."
    )


# ---------------------------------------------------------------------------
# The counterfactual that needs no sample size
# ---------------------------------------------------------------------------


def flag_ablation(as_of: datetime) -> dict:
    """Does carrying a provenance flag cost a founder score? Answered causally, per company.

    For every company holding provenance-flagged evidence: derive the green flags from
    its real events, then derive them again from byte-identical events with the flags
    stripped, and compare the observation the filter would receive. Any difference is
    caused by the flag and nothing else, because nothing else changed.

    This is the check that would have caught the transliterated-name bug on day one, and
    unlike every mean in this file it is valid on a single company.
    """
    from intelligence import flags as flags_mod
    from memory import store

    try:
        companies = store.get_store().companies()
    except Exception as exc:  # noqa: BLE001
        log.info("fairness: store unavailable for ablation (%s)", exc)
        return {"evaluated": False, "reason": f"store unavailable ({exc})", "companies": []}

    from api.routers.deps import founder_entity_ids

    results = []
    for company in companies:
        try:
            events = store.events(company_id=company.company_id, as_of=as_of)
        except Exception:  # noqa: BLE001
            continue
        flagged = [e for e in events if set(e.integrity_flags or []) & set(PROVENANCE_FLAGS)]
        if not flagged:
            continue
        entity_ids = founder_entity_ids(company.company_id)
        if not entity_ids:
            continue
        entity_id = entity_ids[0]
        scoped = [
            e
            for e in events
            if e.entity_id == entity_id and str(e.kind) not in {"green_flag", "integrity"}
        ]
        if not scoped:
            continue
        stripped = [e.model_copy(update={"integrity_flags": []}) for e in scoped]
        with_flags = flags_mod.observation(
            flags_mod.evaluate_events(scoped, entity_id=entity_id, as_of=as_of)
        )
        without_flags = flags_mod.observation(
            flags_mod.evaluate_events(stripped, entity_id=entity_id, as_of=as_of)
        )
        results.append(
            {
                "company": getattr(company, "name", None),
                "flagged_events": len(flagged),
                "flags_present": sorted(
                    {f for e in flagged for f in (e.integrity_flags or [])} & set(PROVENANCE_FLAGS)
                ),
                "y_with_flags": with_flags[0],
                "y_without_flags": without_flags[0],
                "delta_y": with_flags[0] - without_flags[0],
                # CHANGED is the defect, not LOWER. The original bug discarded flagged
                # evidence outright, which leaves the filter with nothing and returns the
                # uninformative prior of 0.5 — and 0.5 is HIGHER than the reading a
                # correctly-scored weak founder earns. A check that only looked for a
                # drop would have watched that founder's evidence be destroyed and
                # called it fine. Any dependence of the reading on a provenance marker
                # is the finding; the direction is a detail reported alongside it.
                "changed": with_flags[0] != without_flags[0],
                "penalised": with_flags[0] < without_flags[0],
            }
        )

    changed = [row for row in results if row["changed"]]
    penalised = [row for row in results if row["penalised"]]
    return {
        "evaluated": bool(results),
        "reason": None if results else "no company in the store carries provenance-flagged evidence",
        "companies": results,
        "n_companies": len(results),
        "n_changed": len(changed),
        "n_penalised": len(penalised),
        "changed_companies": [row["company"] for row in changed],
        "penalised_companies": [row["company"] for row in penalised],
        "verdict": (
            "no provenance flag changes the observation the filter receives"
            if results and not changed
            else (
                f"{len(changed)} company/companies receive a DIFFERENT reading purely "
                f"because their evidence carries a provenance flag "
                f"({len(penalised)} of them scored lower; the rest had their evidence "
                f"discarded and fell back to the uninformative prior, which is not "
                f"better — it is the score forgetting what it was told)"
                if changed
                else "not evaluated"
            )
        ),
        "why_this_is_valid_at_this_n": (
            "This is a paired counterfactual on identical evidence, not a comparison of "
            "groups. The only thing that differs between the two readings is the flag, so "
            "a difference is caused by the flag. It needs no sample size and makes no "
            "claim about a population."
        ),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _outcome_refusals(labelled: Sequence[dict]) -> list[dict]:
    """Every outcome-based fairness metric, and why each one cannot be computed here."""
    international = [row for row in labelled if row.get("archetype") == INTERNATIONAL_ARCHETYPE]
    flagged = [row for row in labelled if row.get("provenance_flags")]
    common = (
        f"The labelled cohort has {len(labelled)} members, of which "
        f"{len(international)} are international and {len(flagged)} carry a provenance flag."
    )
    return [
        dict(
            Refused(
                metric,
                "international / provenance-flagged",
                f"{reason} {common} A rate computed over zero members is not a rate, and a "
                f"rate computed over fewer than {MIN_PER_CLASS_FOR_RATES} per outcome class "
                f"cannot distinguish a real disparity from one member landing either way.",
            )
        )
        for metric, reason in (
            (
                "equal_opportunity_difference",
                "Equal opportunity compares TRUE-POSITIVE rates across groups, which "
                "requires known breakout outcomes within each group.",
            ),
            (
                "false_positive_rate_parity",
                "FPR parity requires known non-breakout outcomes within each group.",
            ),
            (
                "accuracy_parity",
                "Accuracy parity requires labelled outcomes within each group.",
            ),
            (
                "demographic_parity_difference",
                "Selection-rate parity requires enough members per group for a rate to "
                "have more than a handful of attainable values.",
            ),
            (
                "calibration_within_groups",
                "Group-wise calibration requires enough labelled outcomes per group per "
                "probability bin.",
            ),
        )
    ]


def subgroup_report(as_of: datetime, *, labelled: Sequence[dict] | None = None) -> dict:
    """Subgroup fairness over the seeded corpus, with every refusal stated as a result."""
    rows = _population(as_of)
    if not rows:
        return {
            "evaluated": False,
            "reason": "no scoreable company in the store",
            "population": 0,
            "comparisons": [],
            "flag_ablation": {"evaluated": False, "reason": "empty population"},
            "refused": [],
        }

    def axes(subset: Sequence[dict], suffix: str) -> list[dict]:
        if not subset:
            return []
        counts = sorted(row["raw_events"] for row in subset)
        median = counts[len(counts) // 2]
        return [
            _comparison(
                subset,
                lambda row: row["archetype"] == INTERNATIONAL_ARCHETYPE,
                "international",
                "rest_of_corpus",
                f"Type 6 international cohort vs the rest{suffix}",
            ),
            _comparison(
                subset,
                lambda row: row["raw_events"] < median,
                "sparse_evidence",
                "rich_evidence",
                f"sparse vs rich evidence (median raw-event count = {median}){suffix}",
            ),
            _comparison(
                subset,
                lambda row: bool(row["provenance_flags"]),
                "provenance_flagged",
                "unflagged",
                f"provenance-flagged evidence vs unflagged{suffix}",
            ),
        ]

    counts = sorted(row["raw_events"] for row in rows)
    median = counts[len(counts) // 2]
    non_cohort = [row for row in rows if not row["is_cohort"]]
    comparisons = axes(rows, "")
    comparisons_excluding_cohort = axes(non_cohort, ", excluding the backtest cohort")

    # Per-flag axes, so a reader learns which markers are actually exercised in this
    # corpus and which have never been tested at all.
    per_flag = []
    for flag in PROVENANCE_FLAGS:
        carriers = [row for row in rows if flag in row["provenance_flags"]]
        if not carriers:
            per_flag.append(
                dict(
                    Refused(
                        f"mean_mu[{flag}]",
                        flag,
                        f"no event anywhere in the corpus carries `{flag}`. This axis is "
                        f"UNTESTED, not clean: the score's behaviour on evidence with an "
                        f"inferred date has never been measured.",
                    )
                )
            )
        else:
            per_flag.append(_describe(carriers, flag))

    labelled_rows = list(labelled or [])
    collinear = _collinearity(rows)

    return {
        "evaluated": True,
        "as_of": as_of.isoformat(),
        "population": len(rows),
        "median_raw_events": median,
        "comparisons": comparisons,
        "comparisons_excluding_cohort": comparisons_excluding_cohort,
        "sign_stability": _sign_stability(comparisons, comparisons_excluding_cohort),
        "cohort_confound_note": (
            "The backtest cohort was assembled at the extremes — winners because they "
            "broke out, controls because they did not — so any subgroup containing more "
            "of them inherits that spread. Where a gap appears in `comparisons` but not "
            "in `comparisons_excluding_cohort`, the gap is cohort composition rather than "
            "the subgroup attribute."
        ),
        "per_flag": per_flag,
        "collinearity": collinear,
        "flag_ablation": flag_ablation(as_of),
        "refused": _outcome_refusals(labelled_rows),
    }


def _sign_stability(full: Sequence[dict], reduced: Sequence[dict]) -> list[dict]:
    """Does each subgroup gap keep its SIGN when the constructed cohort is removed?

    A gap that reverses direction under a defensible change of population has not
    measured a direction. This is the cheapest available check on whether any of these
    numbers means anything, and at this n it is worth more than any interval would be:
    it cannot tell us the effect size, but it can tell us the effect is not resolvable.
    """
    out = []
    for left, right in zip(full, reduced):
        a, b = left.get("mean_mu_gap"), right.get("mean_mu_gap")
        if a is None or b is None:
            verdict, stable = "not comparable: one population refused the mean", None
        elif (a > 0) == (b > 0):
            verdict, stable = "sign held when the constructed cohort was removed", True
        else:
            verdict, stable = (
                "SIGN REVERSED when the constructed cohort was removed, so the direction of "
                "this gap is a property of which companies are in the corpus, not of the "
                "subgroup. No disadvantage claim can rest on it.",
                False,
            )
        out.append(
            {
                "axis": left.get("axis"),
                "gap_full_corpus": a,
                "gap_excluding_cohort": b,
                "sign_stable": stable,
                "verdict": verdict,
            }
        )
    return out


def _collinearity(rows: Sequence[dict]) -> dict:
    """Are 'international' and 'provenance-flagged' the same set in this corpus?

    If they are, the two comparisons above are one comparison reported twice, and reading
    them as independent corroboration would double-count a single group of three.
    """
    international = {row["name"] for row in rows if row["archetype"] == INTERNATIONAL_ARCHETYPE}
    flagged = {row["name"] for row in rows if row["provenance_flags"]}
    identical = bool(international) and international == flagged
    return {
        "international_members": sorted(international),
        "flagged_members": sorted(flagged),
        "identical": identical,
        "note": (
            "In this corpus every provenance-flagged company is an international company "
            "and vice versa. The 'international' and 'provenance-flagged' comparisons are "
            "therefore the SAME comparison reported twice, and they do not corroborate each "
            "other. Separating the two axes requires a flagged company that is not "
            "international, or an international company whose evidence carries no flag; "
            "the corpus contains neither."
            if identical
            else "The two groups differ, so the comparisons carry some independent content."
        ),
    }


__all__ = [
    "INTERNATIONAL_ARCHETYPE",
    "MIN_DESCRIPTIVE",
    "MIN_PER_CLASS_FOR_RATES",
    "PROVENANCE_FLAGS",
    "Refused",
    "flag_ablation",
    "subgroup_report",
]
