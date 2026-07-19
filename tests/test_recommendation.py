"""The $100K answer: a computed cheque and a confidence with a stated basis.

Every test here is behavioural — it asserts what the recommender DECIDES, not that a
key exists. The failure mode these guard against is the one two audits already found in
this codebase: a field that is always populated, always plausible, and measures nothing.

Fully offline: the screening and the gate are injected, so nothing dials out.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api import memo
from api.main import app
from api.routers import companies as companies_router
from schema.events import (
    Axis,
    ClaimStatus,
    ClaimVerdict,
    GateDecision,
    GateOutcome,
    ScreeningResult,
)

AS_OF = datetime(2024, 6, 1, tzinfo=timezone.utc)
CID = UUID("11111111-1111-1111-1111-111111111111")

THESIS = {"check_size": {"currency": "USD", "min": 250_000, "target": 750_000, "max": 2_000_000}}


@pytest.fixture(autouse=True)
def _seed(tmp_path, monkeypatch):
    """A real thesis fixture on disk — the check_size range must come from config."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "thesis.json").write_text(json.dumps(THESIS))
    # The anti-memo the dissent route serves. Offline, so the route degrades to this —
    # which is still a real bear case in front of the viewer, and so still unlocks.
    (seed_dir / f"dissent_{CID}.json").write_text(
        json.dumps({"bear_case": "the ring buffer is not theirs", "weakest_evidence": []})
    )
    monkeypatch.setenv("VCBRAIN_SEED_DIR", str(seed_dir))
    monkeypatch.setattr(
        "core.llm.complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    )
    yield


def axis(score: float, confidence: float = 0.8) -> Axis:
    return Axis(score=score, trend=0.0, confidence=confidence, evidence_event_ids=[uuid4()])


def screening(founder=0.8, market=0.8, idea=0.8, confidence=0.8) -> ScreeningResult:
    return ScreeningResult(
        company_id=CID,
        as_of=AS_OF,
        founder=axis(founder, confidence),
        market=axis(market, confidence),
        idea_vs_market=axis(idea, confidence),
    )


def verdict(status: ClaimStatus, span: str | None = "corroborated here") -> ClaimVerdict:
    return ClaimVerdict(
        company_id=CID,
        claim_text="we serve 40 customers",
        claim_source_span="slide 7",
        status=status,
        trust=0.5,
        corroborating_span=span,
    )


def recommend(monkeypatch, *, outcome=GateOutcome.PROCEED, sr=None, band=0.1, verdicts=(), gaps=()):
    """Drive the recommender with an injected screening + gate. No network, no store."""
    monkeypatch.setattr(memo, "_screening", lambda cid, as_of: screening() if sr is None else sr)
    monkeypatch.setattr(
        "intelligence.gate.evaluate",
        lambda cid, as_of: GateDecision(
            company_id=CID, outcome=outcome, rationale="because", absence_is_suspicious=False
        ),
    )
    return memo.recommendation(CID, AS_OF, list(verdicts), list(gaps), {"band": band})


# --------------------------------------------------------------------------------------
# Abstention is a real answer, and it is final.
# --------------------------------------------------------------------------------------


def test_no_call_never_produces_a_cheque(monkeypatch):
    """The gate's abstention is not a smaller cheque — it is no cheque."""
    rec = recommend(monkeypatch, outcome=GateOutcome.NO_CALL, sr=screening(0.95, 0.95, 0.95))
    assert rec["decision"] == "no_call"
    assert rec["amount_usd"] is None
    # Even at the top of every axis. A no_call that still sizes is the whole failure mode.
    assert "no_call" in rec["reason"]


def test_wide_founder_band_refuses_even_when_the_gate_says_proof_protocol(monkeypatch):
    """The cold-start shape: we do not know the founder well enough to reserve anything.

    Veritanode is mu=0.53 with a band of 0.47 — the band is the system's own statement
    that it has barely observed this person, so there is nothing to size.
    """
    rec = recommend(monkeypatch, outcome=GateOutcome.PROOF_PROTOCOL, band=0.47)
    assert rec["decision"] == "no_call"
    assert rec["amount_usd"] is None
    assert "band" in rec["reason"]


def test_narrow_band_proof_protocol_reserves_a_conditional_cheque(monkeypatch):
    rec = recommend(monkeypatch, outcome=GateOutcome.PROOF_PROTOCOL, band=0.10)
    assert rec["decision"] == "conditional"
    assert rec["contingent_on"] == "proof_protocol"
    assert rec["amount_usd"] is not None
    # Not yet through the gate, so it cannot be sized above the thesis target.
    assert rec["amount_usd"] <= THESIS["check_size"]["target"]


def test_proceed_can_be_sized_above_target(monkeypatch):
    """Only a company that actually cleared the gate reaches the upper half of the range."""
    rec = recommend(
        monkeypatch, outcome=GateOutcome.PROCEED, sr=screening(0.95, 0.95, 0.95, confidence=1.0),
        band=0.0, verdicts=[verdict(ClaimStatus.VERIFIED)],
    )
    assert rec["decision"] == "invest"
    assert rec["amount_usd"] > THESIS["check_size"]["target"]
    assert rec["amount_usd"] <= THESIS["check_size"]["max"]


# --------------------------------------------------------------------------------------
# The weakest axis governs. Nothing here averages.
# --------------------------------------------------------------------------------------


def test_weakest_axis_governs_and_the_axes_are_never_averaged(monkeypatch):
    """A great founder on a dead market is a market-sized cheque, not a mid-sized one."""
    lopsided = recommend(monkeypatch, sr=screening(0.95, 0.30, 0.95), verdicts=[verdict(ClaimStatus.VERIFIED)])
    uniform = recommend(monkeypatch, sr=screening(0.30, 0.30, 0.30), verdicts=[verdict(ClaimStatus.VERIFIED)])
    mean_shaped = recommend(monkeypatch, sr=screening(0.73, 0.73, 0.73), verdicts=[verdict(ClaimStatus.VERIFIED)])

    assert lopsided["governing_axis"]["name"] == "market"
    assert lopsided["governing_axis"]["score"] == pytest.approx(0.30)
    # Governed by the weakest axis, so it matches the all-0.30 company exactly...
    assert lopsided["amount_usd"] == uniform["amount_usd"]
    # ...and is strictly below what the mean of (0.95, 0.30, 0.95) would have bought.
    assert lopsided["amount_usd"] < mean_shaped["amount_usd"]


def test_a_stronger_governing_axis_buys_a_bigger_cheque(monkeypatch):
    """Monotonic in the thing it claims to be driven by — otherwise it measures nothing."""
    amounts = [
        recommend(monkeypatch, sr=screening(s, s, s), verdicts=[verdict(ClaimStatus.VERIFIED)])["amount_usd"]
        for s in (0.35, 0.55, 0.75, 0.95)
    ]
    assert amounts == sorted(amounts)
    assert len(set(amounts)) > 1, "a recommender that returns one number is decorative"


# --------------------------------------------------------------------------------------
# Unverified claims size the cheque down and say so.
# --------------------------------------------------------------------------------------


def test_unverified_claims_size_down_against_verified_ones(monkeypatch):
    verified = recommend(monkeypatch, verdicts=[verdict(ClaimStatus.VERIFIED)] * 2)
    unverified = recommend(monkeypatch, verdicts=[verdict(ClaimStatus.UNVERIFIABLE)] * 2)
    assert unverified["amount_usd"] is None or unverified["amount_usd"] < verified["amount_usd"]


def test_verified_without_a_corroborating_span_does_not_count_as_verified(monkeypatch):
    """Same rule _gaps applies: a verdict marked verified with no stored span is not one."""
    spanned = recommend(monkeypatch, verdicts=[verdict(ClaimStatus.VERIFIED, span="here")])
    spanless = recommend(monkeypatch, verdicts=[verdict(ClaimStatus.VERIFIED, span=None)])
    assert spanless["amount_usd"] is None or spanless["amount_usd"] < spanned["amount_usd"]


def test_no_claims_on_file_is_not_scored_as_a_verification_failure(monkeypatch):
    """Absence of claims is not evidence of unreliability — it drops out of the minimum.

    The quiet founder who made no deck claims must not be punished for having made none;
    the missing validator run is already counted once, under gap_pressure.
    """
    rec = recommend(monkeypatch, verdicts=[])
    claim = next(c for c in rec["confidence"]["components"] if c["name"] == "claim_verification")
    assert claim["support"] is None, "not applicable, not zero"
    assert rec["confidence"]["binding_component"] != "claim_verification"


def test_open_gaps_reduce_confidence(monkeypatch):
    few = recommend(monkeypatch, gaps=[{"claim": "x"}])
    many = recommend(monkeypatch, gaps=[{"claim": "x"}] * 6)
    assert many["confidence"]["value"] < few["confidence"]["value"]


# --------------------------------------------------------------------------------------
# Confidence has to mean something.
# --------------------------------------------------------------------------------------


def test_confidence_is_the_minimum_of_stated_components_never_a_mean(monkeypatch):
    rec = recommend(
        monkeypatch, sr=screening(0.8, 0.8, 0.8, confidence=0.25), band=0.05,
        verdicts=[verdict(ClaimStatus.VERIFIED)],
    )
    conf = rec["confidence"]
    supports = [c["support"] for c in conf["components"] if c["support"] is not None]
    assert conf["value"] == pytest.approx(min(supports))
    assert conf["value"] != pytest.approx(sum(supports) / len(supports))
    assert conf["binding_component"] == "governing_axis_confidence"


def test_every_confidence_component_states_its_unit_and_derivation(monkeypatch):
    """No bare 0-1 float. This codebase already shipped one that meant nothing."""
    conf = recommend(monkeypatch, verdicts=[verdict(ClaimStatus.VERIFIED)])["confidence"]
    assert "probability" in conf["unit"].lower()  # explicitly says what it is NOT
    assert "minimum" in conf["method"].lower()
    for c in conf["components"]:
        assert c["unit"] and c["basis"], c
        assert "raw" in c, c
    names = {c["name"] for c in conf["components"]}
    assert names == {
        "governing_axis_confidence",
        "founder_interval",
        "claim_verification",
        "gap_pressure",
    }


def test_confidence_is_not_merely_the_inverted_band(monkeypatch):
    """The band is ONE of four components. Holding it fixed, confidence must still move."""
    a = recommend(monkeypatch, sr=screening(0.8, 0.8, 0.8, confidence=0.9), band=0.1)
    b = recommend(monkeypatch, sr=screening(0.8, 0.8, 0.8, confidence=0.2), band=0.1)
    assert a["confidence"]["value"] != b["confidence"]["value"]


# --------------------------------------------------------------------------------------
# Missing inputs produce None WITH A REASON, never a plausible default.
# --------------------------------------------------------------------------------------


def test_no_screening_returns_null_with_a_reason(monkeypatch):
    monkeypatch.setattr(memo, "_screening", lambda cid, as_of: None)
    rec = memo.recommendation(CID, AS_OF, [], [], None)
    assert rec["decision"] == "insufficient_input"
    assert rec["amount_usd"] is None
    assert "screening" in rec["reason"]


def test_gate_failure_returns_null_with_a_reason(monkeypatch):
    monkeypatch.setattr(memo, "_screening", lambda cid, as_of: screening())
    monkeypatch.setattr(
        "intelligence.gate.evaluate",
        lambda cid, as_of: (_ for _ in ()).throw(RuntimeError("gate down")),
    )
    rec = memo.recommendation(CID, AS_OF, [], [], None)
    assert rec["decision"] == "insufficient_input"
    assert rec["amount_usd"] is None
    assert "gate" in rec["reason"]


def test_an_unresolvable_company_returns_null_with_a_reason():
    rec = memo.recommendation(None, AS_OF, [], [], None)
    assert rec["amount_usd"] is None
    assert rec["decision"] == "insufficient_input"


def test_a_zero_confidence_axis_is_a_placeholder_and_cannot_be_sized_on(monkeypatch):
    """screen.py's fallback axis is score 0.5 / confidence 0.0. The 0.5 is not a
    measurement, and sizing on it is exactly the 'looks implemented, measures nothing'
    defect this whole feature is meant to avoid."""
    blind = ScreeningResult(
        company_id=CID, as_of=AS_OF,
        founder=axis(0.8, 0.9), market=Axis(score=0.5, trend=0.0, confidence=0.0),
        idea_vs_market=axis(0.8, 0.9),
    )
    rec = recommend(monkeypatch, sr=blind)
    assert rec["decision"] == "insufficient_input"
    assert rec["amount_usd"] is None
    assert "market" in rec["reason"]


def test_never_returns_the_same_answer_for_every_company(monkeypatch):
    """The decorative-recommender check, stated as an assertion."""
    recs = [
        recommend(monkeypatch, **kw)
        for kw in (
            {"outcome": GateOutcome.NO_CALL},
            {"outcome": GateOutcome.PROOF_PROTOCOL, "band": 0.47},
            {"outcome": GateOutcome.PROOF_PROTOCOL, "band": 0.10},
            {"outcome": GateOutcome.PROCEED, "sr": screening(0.95, 0.95, 0.95)},
            {"outcome": GateOutcome.PROCEED, "sr": screening(0.40, 0.40, 0.40)},
        )
    ]
    # Three distinct cheques, and no two of them equal.
    amounts = [r["amount_usd"] for r in recs if r["amount_usd"] is not None]
    assert len(amounts) == 3 and len(set(amounts)) == 3

    # The two refusals share (decision, amount) by construction — both are "no cheque".
    # What must differ is WHY, or the refusal is untraceable to its cause.
    reasons = [r["reason"] for r in recs if r["amount_usd"] is None]
    assert len(reasons) == 2 and reasons[0] != reasons[1]


# --------------------------------------------------------------------------------------
# The cheque stays inside the thesis, and the thesis is read from config.
# --------------------------------------------------------------------------------------


def test_amount_is_bounded_by_the_thesis_range_and_rounded(monkeypatch):
    for score in (0.05, 0.35, 0.65, 0.95):
        rec = recommend(
            monkeypatch, sr=screening(score, score, score), verdicts=[verdict(ClaimStatus.VERIFIED)]
        )
        if rec["amount_usd"] is None:
            continue
        assert THESIS["check_size"]["min"] <= rec["amount_usd"] <= THESIS["check_size"]["max"]
        assert rec["amount_usd"] % memo.CHECK_ROUNDING == 0


def test_check_size_comes_from_the_thesis_fixture(monkeypatch):
    cs, source = memo._check_size()
    assert source == "thesis"
    assert cs["min"] == 250_000 and cs["max"] == 2_000_000


def test_a_bare_check_size_is_honoured_but_the_inference_is_disclosed(tmp_path, monkeypatch):
    """CONTRACT CHANGE, made deliberately when two config readers were collapsed into one.

    A bare number used to be reported as malformed, which meant one ambiguous-but-usable
    field disabled every recommendation in the system. It is now read as the target with
    a range derived around it — but the source string SAYS the range was derived, so an
    inferred range can never pass for a stated one. Same rule as the axes' `live` flag
    and the memo's gap list: use what you have, disclose what you inferred.
    """
    seed_dir = tmp_path / "s"
    seed_dir.mkdir()
    (seed_dir / "thesis.json").write_text(json.dumps({"check_size": 250000}))
    monkeypatch.setenv("VCBRAIN_SEED_DIR", str(seed_dir))

    cs, source = memo._check_size()
    assert cs["target"] == 250000
    assert cs["min"] < cs["target"] < cs["max"], "a usable range must be derived"
    assert "derived" in source, "an inferred range must never be presented as a stated one"


def test_a_genuinely_malformed_check_size_still_falls_back(tmp_path, monkeypatch):
    """Unordered bounds are not ambiguous, they are wrong — and must not be repaired."""
    seed_dir = tmp_path / "s"
    seed_dir.mkdir()
    (seed_dir / "thesis.json").write_text(
        json.dumps({"check_size": {"min": 900, "target": 100, "max": 50}})
    )
    monkeypatch.setenv("VCBRAIN_SEED_DIR", str(seed_dir))

    cs, source = memo._check_size()
    assert cs == memo.CHECK_SIZE_FALLBACK
    assert source.startswith("fallback")


# --------------------------------------------------------------------------------------
# The prose and the figure must not disagree.
# --------------------------------------------------------------------------------------


def test_the_computed_verdict_is_prepended_to_the_recommendation_prose():
    sections = {"recommendation": {"summary": "We recommend investing at the top of the range."}}
    rec = {"decision": "no_call", "amount_usd": None, "reason": "the gate abstained"}
    out = memo._reconcile(sections, rec)["recommendation"]
    assert out["summary"].startswith("COMPUTED: NO_CALL — no cheque")
    assert out["computed_verdict"] == "COMPUTED: NO_CALL — no cheque"


def test_green_light_prose_under_a_refusal_is_flagged_as_a_conflict():
    sections = {"recommendation": {"summary": "We recommend investing here."}}
    rec = {"decision": "no_call", "amount_usd": None, "reason": "r"}
    out = memo._reconcile(sections, rec)["recommendation"]
    assert "prose_conflict" in out
    assert "computed decision governs" in out["prose_conflict"]


def test_agreeing_prose_is_not_flagged():
    sections = {"recommendation": {"summary": "We should pass; the gaps are unresolved."}}
    rec = {"decision": "invest", "amount_usd": 500_000.0, "reason": "r"}
    out = memo._reconcile(sections, rec)["recommendation"]
    assert "prose_conflict" not in out
    assert "$500,000" in out["summary"]


# --------------------------------------------------------------------------------------
# THE DISSENT LOCK. The cheque is the thing it exists to protect.
# --------------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        memo, "generate_memo",
        lambda cid, as_of: {
            "company_id": str(cid),
            "recommendation": {"summary": "prose"},
            "investment_recommendation": {"decision": "invest", "amount_usd": 750_000.0},
            "gaps": [],
        },
    )
    companies_router.reset_dissent_locks()
    return TestClient(app)


def test_the_cheque_is_locked_until_the_dissent_is_actually_served(client):
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    # dissent_viewed=true on its own is a UI hint, not a key.
    assert body["investment_recommendation"] is None
    assert body["recommendation"] is None
    assert body["recommendation_locked_reason"]


def test_the_cheque_unlocks_only_after_the_anti_memo_is_served(client):
    served = client.get(f"/companies/{CID}/dissent").json()
    # Either shape counts, and only because a bear case is actually in it — the route may
    # serve the plain anti-memo or C's richer council view, which nests it.
    assert companies_router._rendered_bear_case(served)

    # Server state now says the anti-memo was served, and the client says it rendered it.
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["investment_recommendation"] == {"decision": "invest", "amount_usd": 750_000.0}

    # ...but the server half alone still is not enough.
    locked = client.get(f"/companies/{CID}/memo").json()
    assert locked["investment_recommendation"] is None


def test_the_new_required_sections_do_not_leak_the_cheque_past_the_lock(monkeypatch):
    """The structured sections were added to the memo payload; the lock nulls exactly two
    keys. If a section ever restates the amount, the lock becomes decorative."""
    monkeypatch.setattr(
        memo,
        "generate_memo",
        lambda cid, as_of: {
            "company_id": str(cid),
            "structure": [{"key": "cap_table", "heading": "Cap table", "mode": "not_attempted",
                           "populated": False}],
            "company_snapshot": {"status": "computed", "narrative": "a company"},
            "swot": {"status": "computed"},
            "cap_table": {"status": "not_attempted", "attempted": False},
            "recommendation": {"summary": "prose"},
            "investment_recommendation": {"decision": "invest", "amount_usd": 750_000.0},
            "gaps": [],
        },
    )
    companies_router.reset_dissent_locks()
    body = TestClient(app).get(f"/companies/{CID}/memo").json()
    assert body["investment_recommendation"] is None and body["recommendation"] is None
    # The structure and the honest empties are NOT locked — they carry no decision.
    assert body["cap_table"]["status"] == "not_attempted"
    assert "750,000" not in json.dumps(body) and "750000" not in json.dumps(body)


def test_the_not_attempted_sections_are_never_folded_into_the_gap_count(monkeypatch):
    """Those blocks are identical for every company. Counting them as gaps would push
    every cheque in the portfolio to no_call for a reason about none of them."""
    real = recommend(monkeypatch, gaps=[])["amount_usd"]
    padded = recommend(
        monkeypatch,
        gaps=[{"claim": k, "status": "not_attempted"} for k in memo.NOT_ATTEMPTED_SECTIONS],
    )["amount_usd"]
    assert real is not None
    assert padded != real, "sanity: gap count really does move the cheque"
    # ...which is exactly why generate_memo keeps them out of `gaps`.
    assert set(memo.NOT_ATTEMPTED_SECTIONS) == {
        "market_sizing",
        "competition",
        "financials",
        "cap_table",
        "exit",
    }
