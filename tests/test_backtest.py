"""The backtest is proof #1, so its guarantees get tested harder than anything else.

Three claims must hold: no future event ever reaches the scorer, control founders do
not clear the threshold, and every number in the report is one the replay produced.
If the second fails, the score measures fame. If the third fails, the artifact whose
job is proving the system does not fool itself is fooling the reader.

These tests run the REAL path: cohort members are written into a temporary event store
as entities with events, and run_calibration() scores them through memory.score.founder
at successive as_of dates. A test that asserted on a hand-authored trajectory would be
testing the fixture, which is how the fabricated report passed CI for as long as it did.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from backtest import collect, crossval, fairness, runner
from backtest.runner import LookaheadError, assert_no_lookahead, replay, run_calibration
from memory import db, store
from schema.events import CompanyProvenance, Event, EventKind, Source

CUT = datetime(2020, 1, 1, tzinfo=timezone.utc)
PAST = CUT - timedelta(days=30)
FUTURE = CUT + timedelta(days=30)
START = CUT - timedelta(days=360)


def _event(observed_at: datetime, **kw) -> Event:
    return Event(
        kind=kw.pop("kind", EventKind.GREEN_FLAG),
        source=kw.pop("source", Source.GITHUB),
        observed_at=observed_at,
        **kw,
    )


def _readings(values: list[float]) -> list[dict]:
    """Green-flag rollups the scorer consumes, spread evenly over the year before CUT.

    These are sensor readings, not scores: the filter still has to turn them into a
    level and a trend, and the level it produces is not any of these numbers.
    """
    step = (CUT - START) / max(len(values) - 1, 1)
    return [
        {
            "kind": "green_flag",
            "source": "github",
            "observed_at": (START + step * i).isoformat(),
            "payload": {"value": v, "n_flags": 20},
        }
        for i, v in enumerate(values)
    ]


RISING = _readings([0.20, 0.30, 0.42, 0.55, 0.68, 0.80, 0.88, 0.92])
FLAT_LOW = _readings([0.16, 0.15, 0.17, 0.16, 0.15, 0.16, 0.17, 0.16])
FLAT_MID = _readings([0.30, 0.29, 0.31, 0.30, 0.29, 0.30, 0.31, 0.30])


def _member(name: str, label: str, events: list[dict], **extra) -> dict:
    return {
        "id": f"{label}-{name}",
        "name": name,
        "company_name": name,
        "label": label,
        "founder": {"display_name": f"{name} founder", "name_normalized": f"{name} founder"},
        "breakout_at": CUT.isoformat(),
        "collection_cutoff": CUT.isoformat(),
        "events": events,
        **extra,
    }


COHORT = {
    "threshold": 0.6,
    "cohort": [
        _member("winner-a", "winner", RISING),
        _member("winner-b", "winner", RISING),
        _member("control-a", "control", FLAT_LOW),
        _member("control-b", "control", FLAT_MID),
        _member("failure-a", "failure", FLAT_LOW, note="high visibility, no shipping trajectory"),
    ],
}


def _seed(cohort: dict) -> None:
    """Write the cohort into the store the same way scripts/seed.py does."""
    for m in cohort["cohort"]:
        company_id = store.upsert_company(m["company_name"], provenance=CompanyProvenance.SOURCED)
        entity_id = store.upsert_entity(
            m["founder"]["display_name"], m["founder"]["name_normalized"]
        )
        for raw in m["events"]:
            store.append(
                Event(
                    entity_id=entity_id,
                    company_id=company_id,
                    kind=EventKind(raw["kind"]),
                    source=Source(raw["source"]),
                    observed_at=datetime.fromisoformat(raw["observed_at"]),
                    payload=raw["payload"],
                )
            )


def _write_cohort(tmp_path, monkeypatch, cohort: dict, filename: str = "backtest.json") -> None:
    path = tmp_path / filename
    path.write_text(json.dumps(cohort))
    monkeypatch.setattr(collect, "SEED_PATH", path)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    _write_cohort(tmp_path, monkeypatch, COHORT)
    # No live network in tests: B's scanners are real now and will hit rate limits.
    # _scan now returns (signals, errors_by_scanner): a scanner that failed and a
    # scanner that found nothing are different findings. Nothing failed here.
    monkeypatch.setattr(collect, "_scan", lambda founder: ([], {}))
    monkeypatch.setattr(
        "core.llm.complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    )
    yield
    db.reset_connections()


# --- the lookahead assertion ------------------------------------------------


def test_assert_no_lookahead_passes_on_truncated_events() -> None:
    assert assert_no_lookahead([_event(PAST), _event(CUT)], CUT) == 2


def test_assert_no_lookahead_returns_the_number_it_checked() -> None:
    """The count is what lets the report state a real number instead of a literal."""
    assert assert_no_lookahead([], CUT) == 0
    assert assert_no_lookahead([_event(PAST) for _ in range(7)], CUT) == 7


def test_assert_no_lookahead_raises_on_a_future_event() -> None:
    with pytest.raises(LookaheadError) as exc:
        assert_no_lookahead([_event(PAST), _event(FUTURE)], CUT)
    assert "future" in str(exc.value)


def test_lookahead_raises_rather_than_warns(recwarn) -> None:
    with pytest.raises(LookaheadError):
        assert_no_lookahead([_event(FUTURE)], CUT)
    assert not recwarn.list, "a lookahead leak must raise, never warn"


def test_replay_raises_when_the_store_leaks_the_future(monkeypatch) -> None:
    """If as_of scoping regresses anywhere upstream, the rig must fail loudly."""
    company_id = store.upsert_company("Leaky", provenance=CompanyProvenance.SOURCED)
    monkeypatch.setattr(store, "events", lambda **kw: [_event(FUTURE, company_id=company_id)])
    with pytest.raises(LookaheadError):
        replay(company_id, CUT)


def test_replay_uses_the_live_code_path() -> None:
    company_id = store.upsert_company("Ferrite", provenance=CompanyProvenance.SOURCED)
    store.append(_event(PAST, company_id=company_id, payload={"value": 0.7}))
    store.append(_event(FUTURE, company_id=company_id, payload={"value": 0.95}))

    out = replay(company_id, CUT)
    assert out["lookahead_checked"] is True
    assert out["lookahead_events_checked"] >= 1, "the assertion reports what it saw"
    assert out["event_count"] == 1, "the post-cutoff event was not replayed"
    assert out["memo"] is not None and "gaps" in out["memo"]


# --- the replay is a replay -------------------------------------------------


def test_every_cohort_member_takes_the_live_path() -> None:
    """The bug this guards: all 9 members had a null company_id, so every one of them
    fell through to a hand-authored trajectory while the report called itself a replay."""
    _seed(COHORT)
    report = run_calibration()

    assert report["members_replayed"] == report["members_total"] == 5
    assert report["not_replayed"] == []
    assert all(r["replayed"] for r in report["results"])


def test_scores_come_from_the_scorer_not_the_fixture(monkeypatch) -> None:
    """score.founder must actually be called. If it is not, there is no backtest."""
    from memory import score as score_mod

    calls: list[tuple[UUID, datetime]] = []
    real = score_mod.founder
    monkeypatch.setattr(
        score_mod, "founder", lambda e, at: (calls.append((e, at)), real(e, at))[1]
    )

    _seed(COHORT)
    run_calibration()

    assert len(calls) >= 5 * 12, "each member is scored at every cutoff in its series"
    assert len({e for e, _ in calls}) == 5, "every member's own entity was scored"


def test_a_member_absent_from_the_store_is_reported_not_fabricated() -> None:
    """The honest degradation: say the replay did not run. Never substitute numbers."""
    report = run_calibration()  # nothing seeded

    assert report["members_replayed"] == 0
    assert [n["name"] for n in report["not_replayed"]] == [
        m["name"] for m in COHORT["cohort"]
    ]
    assert all(r["trajectory"] == [] and r["peak_mu"] is None for r in report["results"])
    assert report["lookahead_checked"] is False, "nothing ran, so nothing was checked"
    assert report["events_checked"] == 0
    assert report["hit_rate"] is None


def test_lookahead_checked_is_measured_not_asserted() -> None:
    """A literal True here is a false claim in the artifact that exists to prevent them."""
    _seed(COHORT)
    report = run_calibration()

    assert report["events_checked"] > 0
    assert report["lookahead_checked"] is True
    assert sum(r["lookahead_events_checked"] for r in report["results"]) == report[
        "events_checked"
    ]


def test_the_prior_is_not_reported_as_a_score() -> None:
    """mu=0.5 with no observations is the filter saying "I know nothing"."""
    _seed(COHORT)
    report = run_calibration()

    for r in report["results"]:
        for point in r["trajectory"]:
            if not point["n_observations"]:
                assert point["mu"] == pytest.approx(0.5), "unobserved points are the prior"
        assert r["peak_mu"] != pytest.approx(0.5), "the prior never becomes the peak"


# --- calibration ------------------------------------------------------------


def test_run_calibration_reports_the_fame_check() -> None:
    _seed(COHORT)
    report = run_calibration()

    assert report["fame_check_evaluated"] is True
    assert report["fame_check_passed"] is True
    assert report["controls_clearing_threshold"] == []


def test_fame_check_fails_when_a_control_clears_the_threshold(tmp_path, monkeypatch) -> None:
    """The gate must actually be able to fail, or it isn't a gate.

    The control here is given a genuinely rising evidence stream — the same readings as
    a winner. The gate fails because the SCORER rates it highly, not because a number
    was edited into a fixture.
    """
    famous = json.loads(json.dumps(COHORT))
    famous["cohort"][2]["events"] = RISING
    _write_cohort(tmp_path, monkeypatch, famous, "famous.json")
    _seed(famous)

    report = run_calibration()
    assert report["fame_check_passed"] is False
    assert report["controls_clearing_threshold"] == ["control-a founder"]


def test_calibration_reports_hit_rate_and_a_deprioritized_failure() -> None:
    _seed(COHORT)
    report = run_calibration()

    assert report["hit_rate"] == 1.0
    assert report["winners_evaluated"] == 2
    failure = report["correctly_deprioritized_failure"]
    assert failure["name"] == "failure-a"
    assert failure["cleared_threshold"] is False
    assert failure["note"]


def test_winners_rise_and_controls_stay_flat() -> None:
    """The separation the whole pitch rests on, measured rather than drawn."""
    _seed(COHORT)
    report = run_calibration()

    assert all(w["peak_mu"] >= report["threshold"] for w in report["winners"])
    assert all(c["peak_mu"] < report["threshold"] for c in report["controls"])
    for w in report["winners"]:
        scored = [p["mu"] for p in w["trajectory"] if p["n_observations"]]
        assert scored[-1] > scored[0], "the winner's replayed level rises before breakout"


def test_detection_date_is_read_off_the_replay() -> None:
    """`detected_at` used to be recorded in the fixture, which makes it a prediction
    written after the fact rather than a result."""
    _seed(COHORT)
    report = run_calibration()

    for w in report["winners"]:
        assert w["detected_at"], "a cleared winner has a replayed detection date"
        at = datetime.fromisoformat(w["detected_at"])
        assert at <= datetime.fromisoformat(w["truncation_date"])
        point = next(p for p in w["trajectory"] if p["as_of"] == w["detected_at"])
        assert point["mu"] >= report["threshold"]
    for c in report["controls"]:
        assert c["detected_at"] is None


def test_no_controls_is_not_a_pass(tmp_path, monkeypatch) -> None:
    """Vacuous truth would let the whole thesis through unchecked."""
    winners_only = {"threshold": 0.6, "cohort": [COHORT["cohort"][0]]}
    _write_cohort(tmp_path, monkeypatch, winners_only, "winners_only.json")
    _seed(winners_only)

    report = run_calibration()
    assert report["fame_check_evaluated"] is False
    assert report["fame_check_passed"] is False


def test_unreplayed_controls_do_not_count_as_a_pass(tmp_path, monkeypatch) -> None:
    """A control that never ran is not a control that stayed below the line."""
    partial = json.loads(json.dumps(COHORT))
    seeded = {"threshold": 0.6, "cohort": [m for m in partial["cohort"] if m["label"] != "control"]}
    _write_cohort(tmp_path, monkeypatch, partial)
    _seed(seeded)  # winners and the failure only — controls are absent from the store

    report = run_calibration()
    assert report["controls_evaluated"] == 0
    assert report["fame_check_evaluated"] is False
    assert report["fame_check_passed"] is False


def test_trajectories_are_truncated_at_the_cutoff() -> None:
    _seed(COHORT)
    report = run_calibration()

    for r in report["results"]:
        cut = datetime.fromisoformat(r["truncation_date"])
        for point in r["trajectory"]:
            assert datetime.fromisoformat(point["as_of"]) <= cut


# --- collection -------------------------------------------------------------


def test_collect_records_the_truncation_date_explicitly() -> None:
    fp = collect.collect("winner-a", CUT, label="winner")
    assert fp.truncation_date == CUT
    assert fp.as_dict()["truncation_date"] == CUT.isoformat()


def test_collect_drops_post_cutoff_signals() -> None:
    member = {
        "founder": "sig",
        "label": "winner",
        "truncation_date": CUT.isoformat(),
        "signals": [
            {"observed_at": PAST.isoformat(), "url": "keep"},
            {"observed_at": FUTURE.isoformat(), "url": "drop"},
        ],
    }
    collect.SEED_PATH.write_text(json.dumps({"threshold": 0.6, "cohort": [member]}))
    fp = collect.collect("sig", CUT)
    assert [s["url"] for s in fp.raw_signals] == ["keep"]


def test_collect_truncates_scanner_events(monkeypatch) -> None:
    """The scanner path is truncated at collection too, not only at read time."""
    monkeypatch.setattr(collect, "_scan", lambda founder: (["raw"], {}))
    monkeypatch.setattr(collect, "_ingest", lambda signals: ([_event(PAST), _event(FUTURE)], {}))

    fp = collect.collect("sig", CUT)
    assert fp.origin == "scanners"
    assert [e.observed_at for e in fp.events] == [PAST]


def test_load_cohort_resolves_company_ids_from_the_store() -> None:
    """Null company_ids are what routed every member to the fabricated fixture path."""
    assert all(m["company_id"] is None for m in collect.load_cohort()["members"])

    _seed(COHORT)
    members = collect.load_cohort()["members"]
    assert all(m["company_id"] for m in members)
    assert {m["company_id"] for m in members} == {
        str(c["company_id"]) for c in store.all_companies()
    }


def test_load_cohort_raises_without_collected_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collect, "SEED_PATH", tmp_path / "missing.json")
    with pytest.raises(LookupError):
        collect.load_cohort()


# --- cross-validation -------------------------------------------------------
#
# The rule under test is "peak replayed mu >= threshold". The only thing fit from this
# cohort is the threshold, so the only thing that can leak is the threshold, and these
# tests exist to prove each fold really refits it without the member it is judging.


def _calibration() -> dict:
    _seed(COHORT)
    return run_calibration()


def test_loo_produces_one_fold_per_replayed_labelled_member() -> None:
    """Nine points hide behind an average, so the per-fold table is the result."""
    result = crossval.leave_one_out(_calibration())

    assert result["evaluated"] is True
    assert result["n"] == len(COHORT["cohort"])
    assert len(result["folds"]) == result["n"]
    assert {fold["held_out"] for fold in result["folds"]} == {
        m["name"] for m in COHORT["cohort"]
    }
    # Every fold carries its own decision, not just the aggregate.
    for fold in result["folds"]:
        assert fold["fold_threshold"] is not None
        assert fold["predicted_breakout"] is not None
        assert fold["correct"] is not None


def test_each_fold_refits_the_threshold_without_the_member_it_judges() -> None:
    """The held-out member must not influence the threshold used to judge it.

    A fold that reused a threshold fit on all members would be scoring a point that
    helped choose its own cutoff — the same leakage conformal.py drops a company for.
    """
    calibration = _calibration()
    result = crossval.leave_one_out(calibration)
    points = crossval._points(calibration)

    for fold in result["folds"]:
        training = [row for row in points if row["name"] != fold["held_out"]]
        expected, _, _ = crossval.fit_threshold(training)
        assert fold["fold_threshold"] == expected
        # And the fitted value never depends on the held-out score.
        assert all(row["name"] != fold["held_out"] for row in training)


def test_loo_reports_the_threshold_as_a_band_not_a_point() -> None:
    """A separable cohort determines an INTERVAL of thresholds, and saying otherwise
    reports a precision the nine points do not contain."""
    result = crossval.leave_one_out(_calibration())
    band = result["threshold_indeterminacy"]

    assert band["separable"] is True
    assert band["lower"] < band["upper"]
    assert band["width"] > 0
    # The shipped threshold is one convention inside the band, not a measured value.
    assert band["lower"] < result["shipped_threshold"] < band["upper"]


def test_loo_refuses_the_metrics_this_sample_size_cannot_support() -> None:
    """Refusals are results. Each must name itself and carry a reason, never a number."""
    result = crossval.leave_one_out(_calibration())
    refused = {entry["metric"]: entry for entry in result["refused"]}

    assert "roc_auc" in refused
    assert "loo_accuracy_confidence_interval" in refused
    assert "significance_test" in refused
    for entry in refused.values():
        assert entry["refused"] is True
        assert entry["value"] is None
        assert len(entry["reason"]) > 40


def test_loo_refuses_rather_than_scoring_a_cohort_too_small_to_fold() -> None:
    calibration = {"threshold": 0.6, "results": []}
    result = crossval.leave_one_out(calibration)

    assert result["evaluated"] is False
    assert result["folds"] == []
    assert "at least 3" in result["reason"]


def test_fit_threshold_says_so_when_the_classes_overlap() -> None:
    """An overlapping fold must not report itself as cleanly separated."""
    overlapping = [
        {"peak_mu": 0.8, "positive": True},
        {"peak_mu": 0.4, "positive": True},
        {"peak_mu": 0.6, "positive": False},
        {"peak_mu": 0.2, "positive": False},
    ]
    threshold, separable, note = crossval.fit_threshold(overlapping)

    assert threshold is not None
    assert separable is False
    assert "not separable" in note


def test_fit_threshold_refuses_a_single_class_fold() -> None:
    threshold, separable, note = crossval.fit_threshold(
        [{"peak_mu": 0.8, "positive": True}, {"peak_mu": 0.7, "positive": True}]
    )
    assert threshold is None
    assert separable is False
    assert "one class" in note


def test_loo_counts_a_misclassification_when_one_exists() -> None:
    """A perfect score on a separable cohort proves nothing unless the metric can fail."""
    calibration = {
        "threshold": 0.6,
        "results": [
            {"name": n, "label": la, "replayed": True, "peak_mu": mu}
            for n, la, mu in [
                ("w1", "winner", 0.90),
                ("w2", "winner", 0.85),
                ("w3", "winner", 0.11),  # a winner the score missed, buried among controls
                ("c1", "control", 0.10),
                ("c2", "control", 0.15),
                ("c3", "control", 0.12),
            ]
        ],
    }
    result = crossval.leave_one_out(calibration)

    assert result["loo_accuracy"] < 1.0
    assert "w3" in result["misclassified"]
    assert result["threshold_indeterminacy"]["separable"] is False


# --- evidence parity and the H12 verdict ------------------------------------


def test_a_control_thinner_than_its_winner_is_below_the_parity_floor() -> None:
    """A real control's low score is only informative if it had a chance to score high.

    Historical commit volumes and star counts could not be retrieved for the real
    controls, and those fields are inputs several scoring rules read. A control scored
    on a fraction of its winner's evidence is reported, but it does not carry the gate.
    """
    winner = {"id": "w", "name": "W", "events": [{}] * 10}
    thin = {"id": "c", "name": "C", "matched_to": "w", "events": [{}] * 2}
    thick = {"id": "c2", "name": "C2", "matched_to": "w", "events": [{}] * 8}
    by_id = {"w": winner}

    assert runner._evidence_parity(thin, by_id)["sufficient"] is False
    assert runner._evidence_parity(thin, by_id)["ratio"] == 0.2
    assert runner._evidence_parity(thick, by_id)["sufficient"] is True


def test_one_real_control_is_indeterminate_rather_than_a_pass() -> None:
    """A gate that turns on a single company is a fact about that company.

    This is the vacuous-truth failure the fame check already learned once, in a new
    shape: the boolean is true, and it is true because almost nothing could falsify it.
    """
    controls = [
        {
            "name": "Real One",
            "cleared_threshold": False,
            "control_kind": "real",
            "evidence_parity": {"sufficient": True},
        }
    ]
    verdict = runner._fame_check(controls)

    assert verdict["strength"] == "indeterminate"
    assert verdict["real"]["passed"] is True  # the raw assertion still holds ...
    assert "NOT established" in verdict["reading"]  # ... and is reported as not a result


def test_synthetic_controls_alone_are_a_weak_verdict() -> None:
    """Same author wrote both sides, so the comparison cannot be read as historical."""
    verdict = runner._fame_check(
        [{"name": f"S{i}", "cleared_threshold": False, "control_kind": "synthetic"} for i in range(4)]
    )

    assert verdict["strength"] == "weak"
    assert verdict["synthetic"]["passed"] is True
    assert "synthetic controls only" in verdict["reading"]
    assert verdict["requirements_for_a_clean_verdict"]


def test_a_real_control_clearing_the_threshold_fails_the_gate() -> None:
    """H12's whole point: if a real contemporary clears, the score measures fame."""
    verdict = runner._fame_check(
        [
            {
                "name": "Real Clearer",
                "cleared_threshold": True,
                "control_kind": "real",
                "evidence_parity": {"sufficient": True},
            },
            {
                "name": "Real Other",
                "cleared_threshold": False,
                "control_kind": "real",
                "evidence_parity": {"sufficient": True},
            },
        ]
    )

    assert verdict["strength"] == "failing"
    assert verdict["real"]["passed"] is False
    assert "Real Clearer" in verdict["real"]["clearing"]


def test_the_calibration_never_reports_a_bare_pass() -> None:
    """The boolean is preserved for the API contract, but never travels without its
    strength — a consumer reading only `fame_check_passed` is reading a PASS the
    evidence does not support."""
    calibration = _calibration()

    assert "fame_check_passed" in calibration
    assert calibration["fame_check"]["strength"] in {
        "weak",
        "indeterminate",
        "moderate",
        "failing",
    }
    assert calibration["fame_check"]["requirements_for_a_clean_verdict"]


# --- subgroup fairness ------------------------------------------------------
#
# The rule for this whole section: a metric that cannot fail is not a metric. Every
# refusal below is tested for its reason, and the flag ablation is tested against a
# deliberately reintroduced bug so that a clean result means the check works.


def _flagged_company(name: str, *, flags: list[str], archetype: int = 6, n: int = 4):
    company_id = store.upsert_company(name, archetype=archetype, provenance=CompanyProvenance.SOURCED)
    entity_id = store.upsert_entity(f"{name} founder", f"{name} founder".lower())
    step = (CUT - START) / max(n - 1, 1)
    for i in range(n):
        store.append(
            Event(
                entity_id=entity_id,
                company_id=company_id,
                kind=EventKind.RELEASE,
                source=Source.GITHUB,
                observed_at=START + step * i,
                payload={"repo": name, "tag": f"v0.{i}.0"},
                integrity_flags=list(flags),
            )
        )
    return company_id, entity_id


def test_fairness_refuses_a_mean_for_a_group_below_the_floor() -> None:
    """Two companies do not have an average worth printing."""
    _flagged_company("Solo", flags=["transliterated_name"])
    report = fairness.subgroup_report(CUT)
    international = next(
        c for c in report["comparisons"] if c["axis"].startswith("Type 6")
    )["international"]

    assert international["refused"] is True
    assert str(fairness.MIN_DESCRIPTIVE) in international["reason"]
    assert international["value"] is None


def test_fairness_refuses_every_outcome_metric_and_names_the_empty_group() -> None:
    """Equal opportunity, FPR parity and accuracy parity all need labelled outcomes
    inside the group. The group whose fairness most needs checking has none."""
    _flagged_company("Intl", flags=["transliterated_name"])
    report = fairness.subgroup_report(CUT, labelled=[])
    refused = {entry["metric"] for entry in report["refused"]}

    assert {
        "equal_opportunity_difference",
        "false_positive_rate_parity",
        "accuracy_parity",
    } <= refused
    for entry in report["refused"]:
        assert entry["value"] is None
        assert "0 are international" in entry["reason"]


def test_fairness_reports_an_untested_flag_axis_as_untested_not_clean() -> None:
    """No event anywhere carries `date_inferred`. Silence there is absence of testing,
    and reporting it as a clean pass would be the loudest possible false negative."""
    _flagged_company("Intl", flags=["transliterated_name"])
    report = fairness.subgroup_report(CUT)
    entry = next(e for e in report["per_flag"] if e.get("group") == "date_inferred")

    assert entry["refused"] is True
    assert "UNTESTED" in entry["reason"]


def test_fairness_detects_that_the_two_flag_axes_are_the_same_group() -> None:
    """Collinear axes are one comparison reported twice, and must not read as two."""
    _flagged_company("A", flags=["transliterated_name"])
    _flagged_company("B", flags=["non_english_source"])
    _flagged_company("C", flags=["transliterated_name"])
    report = fairness.subgroup_report(CUT)

    assert report["collinearity"]["identical"] is True
    assert "do not corroborate each other" in report["collinearity"]["note"]


def test_flag_ablation_finds_no_penalty_when_flags_are_handled_correctly() -> None:
    """Stripping the provenance flags must not change the reading the filter receives."""
    _flagged_company("Zaryad", flags=["transliterated_name", "non_english_source"])
    result = fairness.flag_ablation(CUT)

    assert result["evaluated"] is True
    assert result["n_changed"] == 0
    assert result["n_penalised"] == 0
    assert all(row["delta_y"] == 0.0 for row in result["companies"])


def test_flag_ablation_catches_the_bug_it_exists_to_catch(monkeypatch) -> None:
    """The original bug, reintroduced: a transliterated name voids the evidence.

    Without this test the ablation above is indistinguishable from a check that fires
    for nobody — which is exactly the failure mode this repo has shipped before.
    """
    from intelligence import flags as flags_mod

    real = flags_mod.evaluate_events

    def blanket_filter(events, *, entity_id, as_of):
        kept = [e for e in events if not (e.integrity_flags or [])]
        return real(kept, entity_id=entity_id, as_of=as_of)

    monkeypatch.setattr(flags_mod, "evaluate_events", blanket_filter)
    _flagged_company("Zaryad", flags=["transliterated_name"])
    result = fairness.flag_ablation(CUT)

    assert result["n_changed"] == 1
    assert result["companies"][0]["delta_y"] != 0.0
    assert "DIFFERENT reading" in result["verdict"]


def test_fairness_flags_a_gap_whose_sign_reverses_without_the_cohort() -> None:
    """A gap that changes direction under a defensible change of population has not
    measured a direction, and no disadvantage claim may rest on it."""
    _seed(COHORT)
    for i in range(3):
        _flagged_company(f"Intl{i}", flags=["transliterated_name"])
    report = fairness.subgroup_report(CUT)

    assert len(report["sign_stability"]) == len(report["comparisons"])
    for entry in report["sign_stability"]:
        assert entry["sign_stable"] in {True, False, None}
        if entry["sign_stable"] is False:
            assert "SIGN REVERSED" in entry["verdict"]


def test_fairness_refuses_significance_on_every_axis() -> None:
    """No test of a difference in means is interpretable at these group sizes."""
    for i in range(3):
        _flagged_company(f"Intl{i}", flags=["transliterated_name"])
    report = fairness.subgroup_report(CUT)

    for comparison in report["comparisons"]:
        assert comparison["significance"]["refused"] is True
        assert comparison["significance"]["value"] is None
