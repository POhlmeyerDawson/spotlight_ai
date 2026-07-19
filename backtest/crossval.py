"""Leave-one-out cross-validation of the whole decision rule. Owner: D, with C.

WHY LEAVE-ONE-OUT AND NOTHING ELSE
----------------------------------
The labelled cohort has twelve members. k-fold with k<n would leave folds of two or
three, and a held-out fold that contains no winner cannot be scored at all; repeated
random splits would just resample the same twelve points and report the variance of
the resampler rather than of the world. Leave-one-out is the only scheme this n admits,
and ``intelligence/conformal.py`` already does per-company leakage exclusion for exactly
this reason. This module extends that idea from the conformal quantile to the DECISION
RULE: hold out one member, refit the threshold on the remaining eleven, and judge the
held-out member with a threshold it had no part in choosing.

WHAT IS ACTUALLY BEING CROSS-VALIDATED
--------------------------------------
The rule is "peak replayed founder-axis mu over the trajectory >= threshold". The scorer
itself is not refit per fold — it has no fitted parameters over this cohort — so the one
thing that CAN leak is the threshold, and the threshold is what each fold refits. The
0.62 the system ships with was calibrated on the archetype corpus, which shares no
members with this cohort; the folds below ignore it and derive their own.

WHY THE PER-FOLD TABLE IS THE RESULT AND THE AVERAGE IS NOT
-----------------------------------------------------------
With nine to twelve points, "LOO accuracy = 0.92" is one member's worth of resolution
per 8 percentage points. The average is reported, but the per-fold rows are the finding,
and the number that actually says how well-determined the rule is is
``threshold_indeterminacy``: the full interval of thresholds that would have produced
IDENTICAL results on every fold. When that interval is wide, the data has not chosen a
threshold — it has chosen a range, and any single value inside it is a convention.

WHAT THIS REFUSES TO EMIT
-------------------------
Every refusal is a code path that returns a reason instead of a number. See ``REFUSALS``.
"""

from __future__ import annotations

from typing import Any, Sequence

# A member counts as a positive example when its outcome label is "winner". Controls and
# the deprioritized failure are both negatives: neither broke out.
POSITIVE_LABEL = "winner"
NEGATIVE_LABELS = frozenset({"control", "failure"})


class Refusal(dict):
    """A metric that was asked for and deliberately not computed.

    It is a dict so it serialises next to the metrics it stands in for, and it always
    carries ``refused: True`` plus a sentence a reader can argue with. A refusal is a
    result — the one thing it must never be is an absent key that reads as an oversight.
    """

    def __init__(self, metric: str, reason: str) -> None:
        super().__init__(refused=True, metric=metric, reason=reason, value=None)


def _peak(result: dict) -> float | None:
    value = result.get("peak_mu")
    return float(value) if isinstance(value, (int, float)) else None


def _points(calibration: dict) -> list[dict]:
    """Replayed, labelled, scored members — the only rows a fold may use.

    A member that did not replay has no score, and a member with no scored trajectory
    point has only the prior. Neither is evidence, and silently treating either as a
    negative would manufacture a passing rule out of a broken rig.
    """
    out = []
    for result in calibration.get("results", []):
        label = str(result.get("label", "")).lower()
        peak = _peak(result)
        if not result.get("replayed") or peak is None:
            continue
        if label != POSITIVE_LABEL and label not in NEGATIVE_LABELS:
            continue
        out.append(
            {
                "name": result.get("name"),
                "label": label,
                "positive": label == POSITIVE_LABEL,
                "peak_mu": peak,
                "control_kind": result.get("control_kind"),
                "evidence_provenance": result.get("evidence_provenance"),
            }
        )
    return sorted(out, key=lambda row: row["peak_mu"])


def fit_threshold(training: Sequence[dict]) -> tuple[float | None, bool, str]:
    """Choose a threshold from the training fold alone. Returns (threshold, separable, how).

    Separable training data admits an interval of perfect thresholds; this takes its
    midpoint, which is the maximum-margin choice and the only one that does not require
    an extra tie-breaking convention. When the classes overlap, it scans every midpoint
    between adjacent scores and takes the one with the fewest training errors, breaking
    ties toward the larger margin.
    """
    positives = [row["peak_mu"] for row in training if row["positive"]]
    negatives = [row["peak_mu"] for row in training if not row["positive"]]
    if not positives or not negatives:
        return None, False, "training fold contains only one class; no threshold is defined"

    low, high = max(negatives), min(positives)
    if low < high:
        return (low + high) / 2.0, True, f"separable: any threshold in ({low:.4f}, {high:.4f})"

    values = sorted({row["peak_mu"] for row in training})
    candidates = [(a + b) / 2.0 for a, b in zip(values, values[1:])] or [values[0]]
    best = min(
        candidates,
        key=lambda t: (
            sum(1 for row in training if row["positive"] != (row["peak_mu"] >= t)),
            -min(abs(row["peak_mu"] - t) for row in training),
        ),
    )
    errors = sum(1 for row in training if row["positive"] != (row["peak_mu"] >= best))
    return best, False, f"not separable; minimum-error threshold makes {errors} training error(s)"


def _indeterminacy(points: Sequence[dict]) -> dict:
    """The full interval of thresholds that classify every member identically.

    This is the honest precision of the threshold. If it spans 0.4 on a 0..1 scale, then
    the cohort has determined the rule to within 0.4 and the shipped 0.62 is one
    convention inside that band rather than a measured value.
    """
    positives = [row["peak_mu"] for row in points if row["positive"]]
    negatives = [row["peak_mu"] for row in points if not row["positive"]]
    if not positives or not negatives:
        return {
            "separable": False,
            "lower": None,
            "upper": None,
            "width": None,
            "note": "only one outcome class is present; no separating interval exists",
        }
    low, high = max(negatives), min(positives)
    if low >= high:
        return {
            "separable": False,
            "lower": None,
            "upper": None,
            "width": None,
            "note": (
                f"classes overlap: the highest non-breakout peak ({low:.4f}) is at or above "
                f"the lowest breakout peak ({high:.4f}), so no threshold separates them"
            ),
        }
    return {
        "separable": True,
        "lower": low,
        "upper": high,
        "width": high - low,
        "note": (
            f"every threshold in ({low:.4f}, {high:.4f}) classifies all {len(points)} "
            f"replayed members identically; the cohort determines the threshold only to "
            f"within this {high - low:.4f}-wide band, and a single shipped value is a "
            f"convention chosen inside it, not a measurement"
        ),
    }


# Metrics that were asked for and are deliberately not computed at this sample size.
# Each entry is (metric, reason) and each is emitted verbatim in the report.
REFUSALS: tuple[tuple[str, str], ...] = (
    (
        "loo_accuracy_confidence_interval",
        "A binomial interval on 12 leave-one-out folds spans roughly +/-25 percentage "
        "points, and the folds are not independent draws from a population — they are 12 "
        "hand-assembled members, most of whose negatives were written by the same author "
        "as the positives. The interval would be arithmetic performed on the sample size, "
        "not evidence about the world.",
    ),
    (
        "roc_auc",
        "The cohort is perfectly separable, so AUC is exactly 1.0 by construction. It "
        "would add no information the separation margin does not already carry, while "
        "reading as a strong result. This repo has already shipped one metric that "
        "returned a confident 1.0 with no discrimination; this is that metric again.",
    ),
    (
        "significance_test",
        "Any p-value here tests the null that 4 winners and 8 non-winners were drawn "
        "exchangeably. They were not drawn at all: the winners were chosen BECAUSE they "
        "broke out and most negatives were composed to contrast with them. The test's "
        "assumption is known to be false, so its output is not interpretable.",
    ),
    (
        "calibration_curve",
        "A reliability diagram needs enough points per probability bin to estimate an "
        "empirical frequency. Twelve points over any usable number of bins leaves one or "
        "two per bin, where the observed frequency can only be 0, 0.5 or 1.",
    ),
    (
        "per_subgroup_loo_accuracy",
        "Splitting 12 folds by subgroup leaves 3 or fewer folds per group, where accuracy "
        "can only take the values 0, 1/3, 2/3 or 1. See backtest/fairness.py, which "
        "refuses the same comparison for the same reason.",
    ),
)


def leave_one_out(calibration: dict) -> dict:
    """LOO over the replayed labelled cohort, refitting the threshold in every fold."""
    points = _points(calibration)
    n = len(points)
    base: dict[str, Any] = {
        "scheme": "leave-one-out",
        "n": n,
        "n_positive": sum(1 for row in points if row["positive"]),
        "n_negative": sum(1 for row in points if not row["positive"]),
        "shipped_threshold": calibration.get("threshold"),
        "refused": [dict(Refusal(metric, reason)) for metric, reason in REFUSALS],
    }
    if n < 3:
        return {
            **base,
            "evaluated": False,
            "reason": (
                f"leave-one-out needs at least 3 replayed labelled members so that a "
                f"training fold can contain both classes; {n} available"
            ),
            "folds": [],
            "threshold_indeterminacy": _indeterminacy(points),
        }

    folds = []
    for index, held_out in enumerate(points):
        training = [row for i, row in enumerate(points) if i != index]
        threshold, separable, how = fit_threshold(training)
        if threshold is None:
            folds.append(
                {
                    "held_out": held_out["name"],
                    "label": held_out["label"],
                    "peak_mu": held_out["peak_mu"],
                    "fold_threshold": None,
                    "training_separable": False,
                    "training_note": how,
                    "predicted_breakout": None,
                    "correct": None,
                    "scored": False,
                }
            )
            continue
        predicted = held_out["peak_mu"] >= threshold
        folds.append(
            {
                "held_out": held_out["name"],
                "label": held_out["label"],
                "control_kind": held_out["control_kind"],
                "peak_mu": held_out["peak_mu"],
                "fold_threshold": threshold,
                "training_separable": separable,
                "training_note": how,
                "predicted_breakout": predicted,
                "correct": predicted == held_out["positive"],
                "margin": held_out["peak_mu"] - threshold,
                "scored": True,
            }
        )

    scored = [fold for fold in folds if fold["scored"]]
    correct = [fold for fold in scored if fold["correct"]]
    thresholds = [fold["fold_threshold"] for fold in scored]
    return {
        **base,
        "evaluated": bool(scored),
        "folds": folds,
        "folds_scored": len(scored),
        "folds_correct": len(correct),
        # Reported as a fraction AND as the raw count, because at this n the fraction is
        # the count in disguise and rounding it hides that.
        "loo_accuracy": (len(correct) / len(scored)) if scored else None,
        "loo_accuracy_basis": f"{len(correct)} of {len(scored)} held-out members classified correctly",
        "misclassified": [fold["held_out"] for fold in scored if not fold["correct"]],
        "fold_threshold_min": min(thresholds) if thresholds else None,
        "fold_threshold_max": max(thresholds) if thresholds else None,
        "fold_threshold_spread": (max(thresholds) - min(thresholds)) if thresholds else None,
        "threshold_indeterminacy": _indeterminacy(points),
    }


__all__ = ["REFUSALS", "Refusal", "fit_threshold", "leave_one_out"]
