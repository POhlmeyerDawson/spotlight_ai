"""The seed data IS the demo. If these fail, every stage beat is running on sand."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory import db, store
from schema.events import CompanyProvenance, EventKind
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
        company_id = store.upsert_company(profile["company_name"], provenance=CompanyProvenance.SOURCED)
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
        prior = store.upsert_company(profile["prior_companies"][0]["name"], provenance=CompanyProvenance.SOURCED)
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


def _backtest_members() -> list[dict]:
    bt = json.loads((SEED_DIR / "backtest.json").read_text(encoding="utf-8"))
    return [*bt["winners"], *bt["controls"], bt["correctly_deprioritized_failure"]]


def test_the_backtest_cohort_states_no_result_it_should_have_to_earn() -> None:
    """The cohort file carries EVIDENCE. Every result is computed by the replay.

    This test replaces one that asserted `gate_h12.status == "PASS"` and compared
    hand-written `founder_score` points against the threshold — verifying that the
    typed numbers agreed with each other, in the artifact whose entire purpose is
    proving the system does not fool itself. The H12 gate is now checked against
    replayed scores in tests/test_backtest.py, where it can actually fail.
    """
    banned = {
        "trajectory",
        "founder_score",
        "peak_score",
        "our_score_at_as_of",
        "cleared_threshold",
        "detected",
        "detected_at",
        "lead_time_days",
        "hit_rate",
    }
    bt = json.loads((SEED_DIR / "backtest.json").read_text(encoding="utf-8"))
    assert not banned & set(bt), "the cohort file states a result it should have to earn"
    assert "status" not in bt["gate_h12"], "the H12 verdict is replayed, never recorded"

    for member in _backtest_members():
        leaked = banned & set(member)
        assert not leaked, f"{member['id']} states {sorted(leaked)} instead of replaying it"
        for event in member["events"]:
            keys = set(event["payload"])
            assert not keys & {"founder_score", "score", "mu", "band", "trend", "rank"}


def test_the_backtest_cohort_can_actually_be_replayed() -> None:
    """Identity + events, or the member silently falls out of the replay.

    Every member of the shipped cohort had a null company_id and no events, so the
    replay resolved nothing and reported hand-authored trajectories instead.
    """
    for member in _backtest_members():
        assert member["company_name"], f"{member['id']} has no company to resolve"
        assert member["founder"]["name_normalized"], f"{member['id']} has no founder entity"
        assert member["events"], f"{member['id']} has nothing to replay"
        assert member["evidence_provenance"] in {
            "reconstructed-from-public-record",
            "synthetic",
        }, f"{member['id']} does not say where its evidence came from"

        cutoff = datetime.fromisoformat(member["collection_cutoff"])
        for event in member["events"]:
            observed = datetime.fromisoformat(event["observed_at"])
            assert observed <= cutoff, (
                f"{member['id']} collected {observed} after its {cutoff} cutoff — "
                "the source truncation is the claim, so it is checked here too"
            )


def test_no_precomputed_scores_leak_into_the_event_fixtures() -> None:
    """The system reads the log. A score in a fixture is a score the pipeline never earned."""
    for path in seed.fixture_files():
        for profile in json.loads(path.read_text(encoding="utf-8"))["profiles"]:
            for event in profile["events"]:
                keys = set(event["payload"])
                assert not keys & {"founder_score", "score", "mu", "band", "trend", "rank"}


# --- provenance: sourced evidence vs authored scenario ----------------------------
#
# The corpus deliberately mixes companies reconstructed from public record with
# companies authored for this repo — the archetype scenarios, and the backtest's
# matched synthetic controls. Both have to exist. What must never happen is a
# constructed company being presentable as sourced evidence, which is what these
# tests hold.


def test_provenance_is_derived_from_the_fixtures_not_a_name_list() -> None:
    """The classification must come out of the data, so a new fixture is covered too.

    A hardcoded list of "the fake ones" is correct the day it is written and wrong the
    first time somebody adds a fixture — and nothing fails when it goes wrong, the
    ranking just quietly starts presenting an authored scenario as sourced evidence.
    """
    source = (SEED_DIR / "provenance.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    _, _, body = code.partition('"""')
    _, _, body = body.partition('"""')  # drop the module docstring, which cites names

    for name in _every_seeded_company_name():
        assert name not in body, (
            f"{name!r} is hardcoded in data/seed/provenance.py. Provenance has to be "
            f"read out of the fixtures, or it stops being true the moment they change."
        )


def _every_seeded_company_name() -> set[str]:
    names: set[str] = set()
    for path in seed.fixture_files():
        for profile in json.loads(path.read_text(encoding="utf-8"))["profiles"]:
            names.add(profile["company_name"])
            names.update(p["name"] for p in profile.get("prior_companies", []))
    for member in _backtest_members():
        names.add(member["company_name"])
    return names


def test_every_archetype_company_is_constructed() -> None:
    """An archetype fixture is authored by definition — including its prior companies."""
    from data.seed.provenance import provenance_for

    for path in seed.fixture_files():
        for profile in json.loads(path.read_text(encoding="utf-8"))["profiles"]:
            for name in [profile["company_name"]] + [
                p["name"] for p in profile.get("prior_companies", [])
            ]:
                assert provenance_for(name) == "constructed", (
                    f"{name} comes from {path.name} and is therefore authored, "
                    f"but is not labelled constructed"
                )


def test_synthetic_cohort_members_are_never_labelled_sourced() -> None:
    """The load-bearing case: synthetic controls sit inside the backtest cohort.

    They carry archetype NULL exactly like the real winners do, so anything inferring
    provenance from `archetype` labels them sourced. They are matched controls for the
    fame-vs-trajectory gate — constructed companies in the middle of the evidence set.
    """
    from data.seed.provenance import provenance_for

    expected = {
        "reconstructed-from-public-record": "sourced",
        "synthetic": "constructed",
    }
    seen = set()
    for member in _backtest_members():
        want = expected[member["evidence_provenance"]]
        seen.add(want)
        assert provenance_for(member["company_name"]) == want, (
            f"{member['company_name']} is {member['evidence_provenance']} in "
            f"backtest.json but resolves to {provenance_for(member['company_name'])}"
        )
    assert seen == {"sourced", "constructed"}, (
        "the cohort must contain both real and synthetic members, or the fame check "
        "has nothing to compare against"
    )


def test_seeding_labels_every_company_in_the_store() -> None:
    """After a load, no company is left without a provenance the API can surface."""
    seed.load()
    companies = store.all_companies()
    assert companies
    for company in companies:
        assert company["provenance"] in {"sourced", "constructed"}, company

    sourced = {c["name"] for c in companies if c["provenance"] == "sourced"}
    real = {
        m["company_name"]
        for m in _backtest_members()
        if m["evidence_provenance"] == "reconstructed-from-public-record"
    }
    assert sourced == real, (
        "the only companies that may be labelled sourced are the ones the cohort file "
        f"claims were reconstructed from public record; got {sorted(sourced - real)} extra"
    )


def test_reseeding_corrects_a_wrong_provenance_rather_than_leaving_it() -> None:
    """A row seeded before the field existed must not keep a stale `sourced` label."""
    from schema.events import CompanyProvenance

    company_id = store.upsert_company("Tensorpage", archetype=1, provenance=CompanyProvenance.SOURCED)
    assert store.get_company(company_id)["provenance"] == "sourced"

    store.upsert_company("Tensorpage", archetype=1, provenance=CompanyProvenance.CONSTRUCTED)
    assert store.get_company(company_id)["provenance"] == "constructed"
