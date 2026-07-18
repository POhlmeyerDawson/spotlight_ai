"""The backtest is proof #1, so its guarantees get tested harder than anything else.

Two claims must hold: no future event ever reaches the scorer, and control founders do
not clear the threshold. If the second fails, the score measures fame.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backtest import collect
from backtest.runner import LookaheadError, assert_no_lookahead, replay, run_calibration
from memory import db, store
from schema.events import Event, EventKind, Source

CUT = datetime(2020, 1, 1, tzinfo=timezone.utc)
PAST = CUT - timedelta(days=30)
FUTURE = CUT + timedelta(days=30)


def _event(observed_at: datetime, **kw) -> Event:
    return Event(
        kind=kw.pop("kind", EventKind.GREEN_FLAG),
        source=kw.pop("source", Source.GITHUB),
        observed_at=observed_at,
        **kw,
    )


def _rising(n: int = 8) -> list[dict]:
    return [
        {
            "as_of": (CUT - timedelta(days=30 * (n - i))).isoformat(),
            "mu": 0.30 + 0.06 * i,
            "band": 0.30 - 0.02 * i,
            "trend": 0.05,
        }
        for i in range(n)
    ]


def _flat(level: float, n: int = 8) -> list[dict]:
    return [
        {
            "as_of": (CUT - timedelta(days=30 * (n - i))).isoformat(),
            "mu": level,
            "band": 0.25,
            "trend": 0.0,
        }
        for i in range(n)
    ]


COHORT = {
    "threshold": 0.6,
    "cohort": [
        {
            "founder": "winner-a",
            "label": "winner",
            "truncation_date": CUT.isoformat(),
            "trajectory": _rising(),
        },
        {
            "founder": "winner-b",
            "label": "winner",
            "truncation_date": CUT.isoformat(),
            "trajectory": _rising(),
        },
        {
            "founder": "control-a",
            "label": "control",
            "truncation_date": CUT.isoformat(),
            "trajectory": _flat(0.34),
        },
        {
            "founder": "control-b",
            "label": "control",
            "truncation_date": CUT.isoformat(),
            "trajectory": _flat(0.41),
        },
        {
            "founder": "failure-a",
            "label": "failure",
            "truncation_date": CUT.isoformat(),
            "trajectory": _flat(0.38),
            "note": "high visibility, no shipping trajectory",
        },
    ],
}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    path = tmp_path / "backtest.json"
    path.write_text(json.dumps(COHORT))
    monkeypatch.setattr(collect, "SEED_PATH", path)
    # No live network in tests: B's scanners are real now and will hit rate limits.
    monkeypatch.setattr(collect, "_scan", lambda founder: [])
    monkeypatch.setattr(
        "core.llm.complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    )
    yield
    db.reset_connections()


# --- the lookahead assertion ------------------------------------------------


def test_assert_no_lookahead_passes_on_truncated_events() -> None:
    assert_no_lookahead([_event(PAST), _event(CUT)], CUT)


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
    company_id = store.upsert_company("Leaky")
    monkeypatch.setattr(store, "events", lambda **kw: [_event(FUTURE, company_id=company_id)])
    with pytest.raises(LookaheadError):
        replay(company_id, CUT)


def test_replay_uses_the_live_code_path() -> None:
    company_id = store.upsert_company("Ferrite")
    store.append(_event(PAST, company_id=company_id, payload={"value": 0.7}))
    store.append(_event(FUTURE, company_id=company_id, payload={"value": 0.95}))

    out = replay(company_id, CUT)
    assert out["lookahead_checked"] is True
    assert out["event_count"] == 1, "the post-cutoff event was replayed"
    assert out["memo"] is not None and "gaps" in out["memo"]


# --- calibration ------------------------------------------------------------


def test_run_calibration_reports_the_fame_check() -> None:
    report = run_calibration()
    assert report["fame_check_evaluated"] is True
    assert report["fame_check_passed"] is True
    assert report["controls_clearing_threshold"] == []


def test_fame_check_fails_when_a_control_clears_the_threshold(tmp_path, monkeypatch) -> None:
    """The gate must actually be able to fail, or it isn't a gate."""
    famous = json.loads(json.dumps(COHORT))
    famous["cohort"][2]["trajectory"] = _flat(0.85)
    path = tmp_path / "famous.json"
    path.write_text(json.dumps(famous))
    monkeypatch.setattr(collect, "SEED_PATH", path)

    report = run_calibration()
    assert report["fame_check_passed"] is False
    assert report["controls_clearing_threshold"] == ["control-a"]


def test_calibration_reports_hit_rate_and_a_deprioritized_failure() -> None:
    report = run_calibration()
    assert report["hit_rate"] == 1.0
    assert report["winners_evaluated"] == 2
    failure = report["correctly_deprioritized_failure"]
    assert failure["founder"] == "failure-a"
    assert failure["cleared_threshold"] is False
    assert failure["note"]


def test_no_controls_is_not_a_pass(tmp_path, monkeypatch) -> None:
    """Vacuous truth would let the whole thesis through unchecked."""
    path = tmp_path / "winners_only.json"
    path.write_text(json.dumps({"threshold": 0.6, "cohort": [COHORT["cohort"][0]]}))
    monkeypatch.setattr(collect, "SEED_PATH", path)

    report = run_calibration()
    assert report["fame_check_evaluated"] is False
    assert report["fame_check_passed"] is False


def test_trajectories_are_truncated_at_the_cutoff() -> None:
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
    monkeypatch.setattr(collect, "_scan", lambda founder: ["raw"])
    monkeypatch.setattr(collect, "_ingest", lambda signals: [_event(PAST), _event(FUTURE)])

    fp = collect.collect("sig", CUT)
    assert fp.origin == "scanners"
    assert [e.observed_at for e in fp.events] == [PAST]


def test_load_cohort_raises_without_collected_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collect, "SEED_PATH", tmp_path / "missing.json")
    with pytest.raises(LookupError):
        collect.load_cohort()
