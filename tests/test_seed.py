"""The seed data IS the demo. If these fail, every stage beat is running on sand."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory import db, store
from schema.events import EventKind
from scripts import seed

ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "data" / "seed"
END_OF_TIME = datetime(2999, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "seed.db"))
    db.reset_connections()
    yield
    db.reset_connections()


def _fixture(archetype: int) -> dict:
    path = next(
        p
        for p in seed.fixture_files()
        if json.loads(p.read_text(encoding="utf-8"))["archetype"] == archetype
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _profiles(archetype: int) -> list[dict]:
    return _fixture(archetype)["profiles"]


# --- loader -----------------------------------------------------------------


def test_loader_is_idempotent() -> None:
    first = seed.load()
    assert first["appended"] > 90
    assert first["skipped"] == 0

    second = seed.load()
    assert second["appended"] == 0
    assert second["skipped"] == first["appended"]
    assert len(store.events(as_of=END_OF_TIME)) == first["appended"]
    assert second["companies"] == first["companies"]
    assert second["entities"] == first["entities"]


def test_every_event_has_a_tz_aware_observed_at() -> None:
    seed.load()
    events = store.events(as_of=END_OF_TIME)
    assert events
    for event in events:
        assert event.observed_at.tzinfo is not None
        assert event.ingested_at.tzinfo is not None


def test_timeline_is_spread_not_stamped_all_at_once() -> None:
    seed.load()
    events = store.events(as_of=END_OF_TIME)
    dates = {e.observed_at.date() for e in events}
    assert len(dates) > 60, "events must be spread over a realistic timeline"
    # Relative, not a fixed year: the loader shifts the corpus forward so founders
    # read as current (see seed._shift). The span is what matters, not the calendar.
    span = max(e.observed_at for e in events) - min(e.observed_at for e in events)
    assert span > timedelta(days=730), "history must cover multiple years"
    # The corpus must end near the present and never in the future: a founder whose
    # newest signal is a year old scores as dormant, which is correct but undemoable.
    newest = max(e.observed_at for e in events)
    now = datetime.now(timezone.utc)
    assert newest <= now, "no event may be observed in the future"
    assert now - newest < timedelta(days=30), "founders must read as currently active"


def test_every_archetype_has_at_least_two_profiles() -> None:
    for archetype in range(1, 7):
        assert len(_profiles(archetype)) >= 2


# --- archetype 2: cold start ------------------------------------------------


def test_cold_start_is_deck_claims_and_essentially_nothing_else() -> None:
    seed.load()
    for profile in _profiles(2):
        company_id = store.upsert_company(profile["company_name"])
        events = store.events(as_of=END_OF_TIME, company_id=company_id)
        kinds = [e.kind for e in events]

        assert kinds.count(EventKind.DECK_CLAIM) >= 3
        # Everything that is not a deck claim is a profile fact read off the same deck.
        assert set(kinds) <= {EventKind.DECK_CLAIM, EventKind.PROFILE_FACT}
        assert all(e.source == "deck" for e in events)
        assert profile["expected_gate"] == "proof_protocol"

    # And the claims are technically specific enough to be worth testing.
    veritanode = next(p for p in _profiles(2) if p["company_id"] == "cs-veritanode")
    claims = [e for e in veritanode["events"] if e["kind"] == "deck_claim"]
    assert all(e["payload"]["falsifiable"] for e in claims)


# --- archetype 4: contradiction, both variants ------------------------------


def _claim_and_counter(profile: dict) -> tuple[datetime, datetime]:
    claim = min(
        (
            e
            for e in profile["events"]
            if e["kind"] == "deck_claim" and "ARR" in e["payload"]["claim"]
        ),
        key=lambda e: e["observed_at"],
    )
    counter = min(
        (e for e in profile["events"] if "pre-revenue" in e["evidence_span"]),
        key=lambda e: e["observed_at"],
    )
    return datetime.fromisoformat(claim["observed_at"]), datetime.fromisoformat(
        counter["observed_at"]
    )


def test_contradiction_has_both_timestamp_variants() -> None:
    variants = {p["variant"]: p for p in _profiles(4)}
    assert set(variants) == {"counter_evidence_newer", "counter_evidence_older"}

    claim_at, counter_at = _claim_and_counter(variants["counter_evidence_newer"])
    assert counter_at > claim_at, "variant (a) must be genuinely contradicted"
    assert variants["counter_evidence_newer"]["expected_verdict"] == "contradicted"

    claim_at, counter_at = _claim_and_counter(variants["counter_evidence_older"])
    assert counter_at < claim_at, "variant (b) is growth, not a lie"
    assert variants["counter_evidence_older"]["expected_verdict"] == "verified_growth"


def test_both_contradiction_variants_make_the_same_claim() -> None:
    """The claim is identical; only the ordering differs. That is the whole test."""
    amounts = {
        e["payload"]["amount_usd"]
        for p in _profiles(4)
        for e in p["events"]
        if e["payload"].get("amount_usd")
    }
    assert amounts == {40000}


# --- archetype 5: adversarial + control -------------------------------------


def test_adversarial_carries_a_detectable_injection() -> None:
    synthgrid = next(p for p in _profiles(5) if p["company_id"] == "adv-synthgrid")
    slide7 = next(e for e in synthgrid["events"] if e["payload"].get("slide") == 7)
    assert "ignore all previous instructions" in slide7["evidence_span"].lower()
    assert slide7["payload"]["raw_text_contains_instruction"] is True
    assert slide7["payload"]["sanitized"] is False, (
        "detection is the pipeline's job, not the fixture's"
    )


def test_adversarial_burst_is_high_volume_and_low_substance() -> None:
    synthgrid = next(p for p in _profiles(5) if p["company_id"] == "adv-synthgrid")
    burst = next(e for e in synthgrid["events"] if e["kind"] == "commit_burst")["payload"]
    assert burst["commits"] > 3000
    assert burst["net_lines"] < 1000
    assert burst["tests_added"] == 0
    assert burst["whitespace_only_pct"] > 0.5


def test_adversarial_control_has_a_bigger_burst_with_real_substance() -> None:
    profiles = {p["company_id"]: p for p in _profiles(5)}
    control = profiles["adv-control-ferrite"]
    assert control["is_control_for"] == "adv-synthgrid"
    assert control["expected_integrity_flags"] == []

    fake = next(e for e in profiles["adv-synthgrid"]["events"] if e["kind"] == "commit_burst")[
        "payload"
    ]
    real = next(e for e in control["events"] if e["kind"] == "commit_burst")["payload"]

    # More commits than the adversarial burst - so volume alone cannot be the signal.
    assert real["commits"] > fake["commits"]
    assert real["net_lines"] > 50 * fake["net_lines"]
    assert real["tests_added"] > 500
    assert real["whitespace_only_pct"] < 0.05


# --- archetype 6: invisible international -----------------------------------


def test_international_profiles_carry_native_and_romanized_names() -> None:
    profiles = _profiles(6)
    assert len(profiles) >= 2
    for profile in profiles:
        assert profile["company_name_native"] != profile["company_name"]
        for founder in profile["founders"]:
            assert founder["name_native"] and founder["name"]
            assert founder["name_native"] != founder["name"]
            assert not founder["name_native"].isascii(), "native form must be non-Latin script"
            assert founder["name"].isascii(), "romanized form must be Latin script"
            assert founder["romanization_variants"]
            assert founder["name_normalized"] == founder["name"].lower()


def test_international_profiles_have_non_english_sources_and_substance() -> None:
    for profile in _profiles(6):
        flags = {f for e in profile["events"] for f in e.get("integrity_flags", [])}
        assert "transliterated_name" in flags
        assert "non_english_source" in flags
        kinds = {e["kind"] for e in profile["events"]}
        assert {"repo_activity", "release"} <= kinds, "low visibility, real technical substance"


# --- archetype 3: founder history survives the company boundary -------------


def test_serial_founder_history_persists_across_companies() -> None:
    seed.load()
    for profile in _profiles(3):
        entity_id = store.upsert_entity(
            profile["founders"][0]["name"], profile["founders"][0]["name_normalized"]
        )
        events = store.events(as_of=END_OF_TIME, entity_id=entity_id)
        company_ids = {e.company_id for e in events}
        assert len(company_ids) == 2, "prior-company events must hang off the same entity"
        prior = store.upsert_company(profile["prior_companies"][0]["name"])
        prior_events = [e for e in events if e.company_id == prior]
        assert prior_events
        # The prior company must be genuinely PRIOR — asserted against the new
        # company's own events rather than a fixed year, which the shift moves.
        current = [e for e in events if e.company_id != prior]
        assert current, "serial founder must have events on the new company too"
        assert max(e.observed_at for e in prior_events) < max(e.observed_at for e in current), (
            "prior-company history must predate the current company's latest activity"
        )


# --- API fixtures the dashboard reads ---------------------------------------


def test_api_fixtures_exist_for_every_stage_archetype() -> None:
    for company_id in (
        "cs-veritanode",
        "cx-arcwell",
        "adv-synthgrid",
        "intl-zaryad",
        "vb-tensorpage",
    ):
        for prefix in ("company", "memo", "dissent"):
            assert (SEED_DIR / f"{prefix}_{company_id}.json").exists()
    for name in ("thesis", "companies", "backtest"):
        assert (SEED_DIR / f"{name}.json").exists()


def test_ranked_list_never_carries_a_blended_score() -> None:
    entries = json.loads((SEED_DIR / "companies.json").read_text(encoding="utf-8"))["companies"]
    assert len(entries) >= 12
    for entry in entries:
        assert "score" not in entry
        assert "overall" not in entry and "blended_score" not in entry
        assert set(entry["axes"]) == {"founder", "market", "idea_vs_market"}
        assert "momentum" in entry


def test_memos_flag_gaps_and_cite_events() -> None:
    for path in SEED_DIR.glob("memo_*.json"):
        memo = json.loads(path.read_text(encoding="utf-8"))
        assert {"thesis", "founder", "market", "risks", "recommendation"} <= set(memo["sections"])
        assert memo["sections"]["gaps_flagged"], f"{path.name} flags no gaps"
        assert memo["citations"], f"{path.name} cites nothing"
        assert memo["recommendation"]


def test_backtest_controls_stay_below_threshold() -> None:
    """H12 hard gate: if a control clears, the score measures fame, not trajectory."""
    bt = json.loads((SEED_DIR / "backtest.json").read_text(encoding="utf-8"))
    threshold = bt["threshold"]["value"]

    for control in bt["controls"]:
        scores = [p["founder_score"] for p in control["trajectory"]]
        assert max(scores) < threshold, f"{control['id']} clears the threshold"
        assert max(scores) - min(scores) < 0.1, f"{control['id']} is not flat"
        assert control["cleared_threshold"] is False

    assert bt["gate_h12"]["status"] == "PASS"
    assert bt["gate_h12"]["max_control_score_observed"] < threshold

    detected = [w for w in bt["winners"] if w["detected"]]
    assert len(detected) >= 3
    for winner in detected:
        scores = [p["founder_score"] for p in winner["trajectory"]]
        assert scores[-1] > threshold
        assert scores[-1] - scores[0] > 0.3, "winners must rise, not sit high"

    assert bt["correctly_deprioritized_failure"]["cleared_threshold"] is False


def test_no_precomputed_scores_leak_into_the_event_fixtures() -> None:
    """The system reads the log. A score in a fixture is a score the pipeline never earned."""
    for path in seed.fixture_files():
        for profile in json.loads(path.read_text(encoding="utf-8"))["profiles"]:
            for event in profile["events"]:
                keys = set(event["payload"])
                assert not keys & {"founder_score", "score", "mu", "band", "trend", "rank"}
