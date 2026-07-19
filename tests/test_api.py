"""Every route answers, and the dissent lock cannot be talked out of. Owner: D."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import companies as companies_router
from memory import db

T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
CID = "11111111-1111-1111-1111-111111111111"
EID = "22222222-2222-2222-2222-222222222222"

FIXTURES = {
    "thesis": {"sectors": ["infra"], "stage": "pre-seed", "check_size": 250000},
    "companies": {
        "companies": [
            {
                "company_id": CID,
                "name": "Ferrite",
                "sector": "infra",
                "trend": 0.4,
                "mu": 0.71,
                "gate": "proceed",
                "claims": [{"status": "not_attempted", "claim_text": "$40K ARR"}],
            },
            {
                "company_id": "33333333-3333-3333-3333-333333333333",
                "name": "Placid",
                "sector": "fintech",
                "trend": -0.2,
                "mu": 0.30,
                "gate": "no_call",
                "claims": [{"status": "verified", "claim_text": "2 customers"}],
            },
        ]
    },
    f"company_{CID}": {
        "company_id": CID,
        "name": "Ferrite",
        "events": [
            {
                "event_id": EID,
                "kind": "commit_burst",
                "source": "github",
                "source_url": "https://github.com/ferrite/core/commit/abc123",
                "observed_at": T0.isoformat(),
                "evidence_span": "abc123 — 'replace the lock-free ring buffer with a "
                "wait-free variant'",
                "confidence": 0.9,
            }
        ],
    },
    f"memo_{CID}": {
        "company_id": CID,
        "thesis": {"summary": "s", "claims": []},
        "recommendation": {"summary": "invest"},
        "gaps": [],
    },
    f"dissent_{CID}": {
        "company_id": CID,
        "bear_case": "b",
        "weakest_evidence": [],
        "load_bearing_claim": "the buffer is theirs",
    },
    "hidden": {"candidates": [], "access_lift": 0.62},
    "challenge": {
        "challenge_id": "44444444-4444-4444-4444-444444444444",
        "prompt": "p",
        "central_claim": "c",
        "ambiguous_requirement": "a",
        "planted_bad_constraint": "b",
    },
    "proof_result": {"graded_event_ids": []},
    "backtest": {"threshold": 0.6, "results": [], "fame_check_passed": True},
}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for name, blob in FIXTURES.items():
        (seed_dir / f"{name}.json").write_text(json.dumps(blob))
    monkeypatch.setenv("VCBRAIN_SEED_DIR", str(seed_dir))
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    companies_router.reset_dissent_locks()
    # No live network in tests, on any path.
    monkeypatch.setattr(
        "core.llm.complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    )
    yield
    db.reset_connections()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    """Liveness, plus the diagnostics that exist so quiet degradations are visible.

    Asserted by invariant rather than by exact shape: /health is deliberately the
    place new warnings get added, and pinning it to an exact dict makes every such
    addition look like a regression.
    """
    body = client.get("/health").json()
    assert body["ok"] is True
    assert "github_authenticated" in body
    # The check must never be the thing that takes health down.
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/thesis",
        "/companies",
        f"/companies/{CID}",
        f"/companies/{CID}/trace/{EID}",
        f"/companies/{CID}/score-history",
        f"/companies/{CID}/memo",
        f"/companies/{CID}/dissent",
        "/hidden",
        "/query?q=infra founders with rising trend and unverified revenue",
        "/backtest",
    ],
)
def test_get_routes_answer(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200, path


@pytest.mark.parametrize(
    "path",
    [
        f"/companies/{CID}/proof",
        f"/companies/{CID}/proof/44444444-4444-4444-4444-444444444444/grade",
    ],
)
def test_post_routes_answer(client: TestClient, path: str) -> None:
    assert client.post(path, json={"demo": True}).status_code == 200, path


# --- the dissent lock -------------------------------------------------------


def test_memo_locked_by_default(client: TestClient) -> None:
    body = client.get(f"/companies/{CID}/memo").json()
    assert body["recommendation"] is None
    assert body["recommendation_locked_reason"]


def test_dissent_viewed_flag_alone_cannot_unlock(client: TestClient) -> None:
    """The frontend claiming the dissent was read is not evidence that it was served."""
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is None, "the lock was bypassable from the client"


def test_unlocks_only_after_dissent_is_served(client: TestClient) -> None:
    assert client.get(f"/companies/{CID}/dissent").status_code == 200
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is not None
    assert "recommendation_locked_reason" not in body


def test_lock_is_per_company(client: TestClient) -> None:
    """Serving one company's dissent must not unlock a DIFFERENT company's cheque.

    `other` has to be a company that actually exists. It used to be an unseeded UUID,
    which passed for the wrong reason: an unknown company now 404s, so the assertion
    was reading an error body rather than a locked memo, and would have kept passing
    even if the lock leaked across every real company in the corpus.
    """
    other = "vb-tensorpage"
    assert client.get(f"/companies/{other}/memo").status_code == 200, "other must exist"
    client.get(f"/companies/{CID}/dissent")
    body = client.get(f"/companies/{other}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is None, "one company's dissent unlocked another's"
    assert body["investment_recommendation"] is None, "the cheque figure leaked"
    assert body["recommendation_locked_reason"]


# --- trace, memo content, query --------------------------------------------


def test_trace_bottoms_out_in_a_quoted_span(client: TestClient) -> None:
    body = client.get(f"/companies/{CID}/trace/{EID}").json()
    assert body["has_span"] is True
    assert "wait-free variant" in body["quoted_span"]
    assert body["source_url"].startswith("https://")
    steps = [s["step"] for s in body["chain"]]
    assert steps == ["score", "event", "source span", "original"]


def test_memo_flags_gaps_rather_than_filling_them(client: TestClient) -> None:
    client.get(f"/companies/{CID}/dissent")
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    for section in ("thesis", "founder", "market", "risks", "recommendation"):
        assert section in body, section
    assert isinstance(body["gaps"], list) and body["gaps"], "a memo with no gap list is fabricating"
    assert any(
        "not_attempted" in g["status"] or "unverifiable" in g["status"] for g in body["gaps"]
    )


def test_score_history_returns_a_series(client: TestClient) -> None:
    body = client.get(f"/companies/{CID}/score-history").json()
    assert "series" in body and isinstance(body["series"], list)


def test_query_filters_in_python(client: TestClient) -> None:
    body = client.get("/query", params={"q": "infra founders with rising trend"}).json()
    names = [c["name"] for c in body["results"]]
    assert names == ["Ferrite"], f"filter let the wrong companies through: {names}"


def test_query_unverified_filter(client: TestClient) -> None:
    body = client.get("/query", params={"q": "companies with unverified revenue"}).json()
    assert [c["name"] for c in body["results"]] == ["Ferrite"]


def test_routes_survive_every_module_being_unimplemented(client: TestClient) -> None:
    """The demo must not depend on four services being simultaneously healthy."""
    assert client.get("/hidden").json()["access_lift"] == 0.62
    assert client.get(f"/companies/{CID}").status_code == 200


def test_as_of_is_honoured_on_score_history(client: TestClient) -> None:
    past = (T0 - timedelta(days=365)).isoformat()
    assert client.get(f"/companies/{CID}/score-history", params={"as_of": past}).status_code == 200


# --- serving what we compute, and disclosing what we don't ------------------


def _axes(payload: dict) -> list[tuple[str, dict]]:
    return list((payload.get("axes") or {}).items())


def test_no_axis_ever_pads_evidence_with_placeholders(client: TestClient) -> None:
    """A padded receipt renders as a clickable trace that drills into nothing.

    Empty strings in evidence_event_ids were served on market and idea-vs-market for
    EVERY company — 5-9 fake receipts each. An axis with no receipts must say so.
    """
    for payload in [client.get("/companies").json()[0], client.get(f"/companies/{CID}").json()]:
        for name, axis in _axes(payload):
            ids = axis["evidence_event_ids"]
            assert all(str(i).strip() for i in ids), f"{name} padded its receipts: {ids}"


def test_every_axis_discloses_whether_it_was_computed(client: TestClient) -> None:
    """The `live` flag the docstring promised. Documented-but-absent is worse than absent."""
    for payload in [client.get("/companies").json()[0], client.get(f"/companies/{CID}").json()]:
        for name, axis in _axes(payload):
            assert isinstance(axis.get("live"), bool), f"{name} does not disclose live-ness"
            # An axis with no receipts must state why rather than just looking empty.
            if not axis["evidence_event_ids"]:
                assert axis.get("reason"), f"{name} is empty without saying why"


def test_gate_says_where_it_came_from(client: TestClient) -> None:
    """The engine must win over the fixture, and a fallback must be VISIBLE.

    The fixture authored `proceed` for Ferrite. With no store behind it the engine
    cannot answer, so the fallback is legitimate — but it has to be declared, not
    served as though the decision engine had produced it.
    """
    row = next(c for c in client.get("/companies").json() if c["name"] == "Ferrite")
    assert row["gate_source"] in {"computed", "seeded_fixture", "unavailable"}
    if row["gate_source"] != "computed":
        assert row["gate_rationale"] is None or isinstance(row["gate_rationale"], str)
    # Whatever happens, the gate is never silently invented as the permissive verdict.
    assert row["gate"] in {"proceed", "no_call", "proof_protocol", None}


def test_gate_is_not_defaulted_to_proceed_when_unknown() -> None:
    """An absence of data must not become the most permissive verdict in the system."""
    from api.main import _gate_for

    gate, source, _ = _gate_for(None, datetime.now(timezone.utc), {})
    assert gate is None and source == "unavailable"


# --- the council must not open the lock it exists to enforce ----------------


def test_empty_council_does_not_unlock_the_recommendation(client: TestClient) -> None:
    """council.deliberate() returns decision=None/anti_memo=None BY DESIGN — it is the
    LOCKED view. Unlocking on it made the endpoint that represents the lock the one
    endpoint that bypassed it."""
    body = client.post(f"/companies/{CID}/council").json()
    assert body.get("anti_memo") is None, "fixture assumption: this council shows no bear case"
    memo = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert memo["recommendation"] is None, "an empty council unlocked the recommendation"


def test_council_that_argues_a_bear_case_does_unlock(client: TestClient) -> None:
    from api.routers import companies as mod

    assert mod._rendered_bear_case({"anti_memo": {"bear_case": "the buffer is not theirs"}})
    assert mod._rendered_bear_case({"bear_case": "thin evidence"})
    assert not mod._rendered_bear_case({"anti_memo": None, "decision": None})
    assert not mod._rendered_bear_case({"anti_memo": {"bear_case": "   "}})


# --- ranking, trend units, and the trace hop -------------------------------


def test_rank_order_is_monotonic_under_the_declared_policy() -> None:
    """min_axis_with_momentum_tiebreak: weakest axis first, momentum only to break ties.

    Founder score alone must NOT predict order — a company can lead on founder and
    still rank lower because its market axis is weaker. That is the policy working.
    """
    from api.main import _rank_key

    def row(f, m, i, trend):
        return {
            "axes": {
                "founder": {"score": f, "trend": trend},
                "market": {"score": m},
                "idea_vs_market": {"score": i},
            }
        }

    rows = [row(66.3, 60.0, 67.0, 1.94), row(63.7, 62.0, 61.0, 0.65), row(73.8, 72.0, 76.0, 1.25)]
    ordered = sorted(rows, key=_rank_key)
    mins = [min(a["score"] for a in r["axes"].values()) for r in ordered]
    assert mins == sorted(mins, reverse=True), f"weakest-axis order violated: {mins}"
    # The 66.3-founder row ranks BELOW the 63.7 one because its market axis is 60 < 62.
    assert [r["axes"]["founder"]["score"] for r in ordered] == [73.8, 63.7, 66.3]


def test_trend_is_reported_per_30_days_not_per_year() -> None:
    """FounderScore.trend is score-units PER YEAR (memory.score runs dt in years).
    Treating it as per-day rendered every trend 365.25x too large."""
    from api.main import _TREND_YEARS_PER_30_DAYS

    assert _TREND_YEARS_PER_30_DAYS == pytest.approx(30.0 / 365.25)
    # A per-year trend of 0.19 is ~1.56 in per-30-day score units, not 568.
    assert 0.19 * 100 * _TREND_YEARS_PER_30_DAYS == pytest.approx(1.56, abs=0.05)


def test_screen_axes_do_not_fabricate_a_band_or_inflate_direction() -> None:
    """The screen produces no uncertainty band and its trend is a -1..1 DIRECTION.

    Deriving a band from confidence would draw a made-up interval in the same units as
    the founder axis's real filter-computed band; rescaling the direction by 100 gave
    market a trend of 100.0 on a 0..100 axis.
    """
    from types import SimpleNamespace

    from api.main import TREND_UNIT_DIRECTION, _axis_from_screen

    axis = _axis_from_screen(
        SimpleNamespace(score=0.8, trend=1.0, confidence=0.7, evidence_event_ids=["abc"])
    )
    assert axis["band"] is None, "a band the screen never computed was invented"
    assert axis["trend"] == 1.0, "a directional trend was rescaled into an impossible rate"
    assert axis["trend_unit"] == TREND_UNIT_DIRECTION
    assert axis["live"] is True and axis["evidence_event_ids"] == ["abc"]


def test_seeded_axis_serializes_an_unmeasured_axis_as_null_not_zero() -> None:
    """An authored null must reach the client as null, on EVERY endpoint.

    `score or 0.0` turned "no observable evidence — not a zero, an absence" into a
    confident 0.0, which `_rank_key` reads as a real measurement (it tests isinstance
    float), promoting an unmeasured company into the fully-measured tier and sorting it
    last inside it — the inverse of the stated policy — while the UI rendered "0 ±0.0".
    Nothing checked this, which is the only reason it shipped.
    """
    from api.main import _rank_key, _rescale_axis

    out = _rescale_axis(
        {"score": None, "trend": None, "band": None, "confidence": 0.0, "evidence": []}
    )
    assert out["score"] is None, "an absence was coalesced into a confident zero"
    assert out["band"] is None, "a missing interval became a claim of perfect certainty"
    assert out["trend"] is None

    # The rank tier follows: an axis that did not measure must not look measured.
    unmeasured = {"axes": {"founder": {"score": 70.0}, "market": {"score": 60.0},
                           "idea_vs_market": out}}
    measured = {"axes": {k: {"score": 10.0} for k in ("founder", "market", "idea_vs_market")}}
    assert _rank_key(measured) < _rank_key(unmeasured), (
        "an unmeasured axis was ranked as if it had been measured"
    )


def test_seeded_axis_trend_is_scaled_by_its_own_unit_and_always_labelled() -> None:
    """`trend` must never be rescaled by a factor chosen from a DIFFERENT quantity.

    One serializer picked x100-or-x1 by looking at whether `score` was <= 1.0 and then
    applied it to `trend`, so one unlabelled field carried two incompatible scales and
    `trend_unit` was dropped entirely — the consumer could not tell which it received.
    """
    from api.main import TREND_UNIT_DIRECTION, TREND_UNIT_SCORE_PER_30D, _rescale_axis

    rate = _rescale_axis({"score": 0.77, "trend": -0.06, "band": 0.11, "confidence": 0.7})
    assert rate["trend"] == -6.0 and rate["trend_unit"] == TREND_UNIT_SCORE_PER_30D

    # A trend stays measurable even when the score beside it is not.
    unscored = _rescale_axis({"score": None, "trend": 0.05, "confidence": 0.1})
    assert unscored["trend"] == 5.0, "trend was scaled by a factor derived from score"
    assert unscored["trend_unit"] == TREND_UNIT_SCORE_PER_30D

    # An authored direction is a sign, not a rate, and keeps both its value and its unit.
    direction = _rescale_axis(
        {"score": 0.5, "trend": 1.0, "trend_unit": TREND_UNIT_DIRECTION, "confidence": 0.4}
    )
    assert direction["trend"] == 1.0, "a direction was inflated into an impossible rate"
    assert direction["trend_unit"] == TREND_UNIT_DIRECTION


def test_trace_never_presents_a_generated_summary_as_a_receipt() -> None:
    """A green-flag rollup's span is a sentence the system wrote about itself. It must
    be labelled as such, and the real receipt comes from the events it rolled up."""
    from api.routers.companies import _trace_payload

    common = dict(
        company_id="c", event_id="e", kind="green_flag", source="derived",
        observed_at=T0.isoformat(), confidence=1.0, integrity_flags=[], payload={},
        contributing_to=None, source_url=None, degraded=False,
    )
    # A rollup with nothing behind it has NO receipt — has_span must be honest.
    bare = _trace_payload(quoted_span="1/24 applicable green flags fired",
                          span_is_generated=True, **common)
    assert bare["has_span"] is False
    assert "GENERATED BY THIS SYSTEM" in bare["chain"][2]["detail"]

    # Following the hop lands on a real commit span, and that becomes the receipt.
    hopped = _trace_payload(
        quoted_span="1/24 applicable green flags fired",
        span_is_generated=True,
        underlying_evidence=[{
            "event_id": "u", "kind": "repo_activity", "source": "github",
            "source_url": "https://github.com/tensorpage/pagekv/commits/main",
            "quoted_span": 'commit 4b91e0c "pagekv: block table with refcounted pages"',
            "observed_at": T0.isoformat(),
        }],
        **common,
    )
    assert hopped["has_span"] is True
    assert "4b91e0c" in next(s["detail"] for s in hopped["chain"] if s["step"] == "source span")
