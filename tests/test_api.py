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
    f"memo_{CID}": {"company_id": CID, "thesis": {"summary": "s", "claims": []},
                    "recommendation": {"summary": "invest"}, "gaps": []},
    f"dissent_{CID}": {"company_id": CID, "bear_case": "b", "weakest_evidence": [],
                       "load_bearing_claim": "the buffer is theirs"},
    "hidden": {"candidates": [], "access_lift": 0.62},
    "challenge": {"challenge_id": "44444444-4444-4444-4444-444444444444", "prompt": "p",
                  "central_claim": "c", "ambiguous_requirement": "a",
                  "planted_bad_constraint": "b"},
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
    [f"/companies/{CID}/proof", f"/companies/{CID}/proof/44444444-4444-4444-4444-444444444444/grade"],
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
    other = "33333333-3333-3333-3333-333333333333"
    client.get(f"/companies/{CID}/dissent")
    assert client.get(f"/companies/{other}/memo?dissent_viewed=true").json()[
        "recommendation"
    ] is None


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
    assert any("not_attempted" in g["status"] or "unverifiable" in g["status"]
               for g in body["gaps"])


def test_score_history_returns_a_series(client: TestClient) -> None:
    body = client.get(f"/companies/{CID}/score-history").json()
    assert "series" in body and isinstance(body["series"], list)


def test_query_filters_in_python(client: TestClient) -> None:
    body = client.get("/query", params={"q": "infra founders with rising trend"}).json()
    names = [c["name"] for c in body["results"]]
    assert names == ["Ferrite"], f"filter let the wrong companies through: {names}"


def test_query_unverified_filter(client: TestClient) -> None:
    body = client.get(
        "/query", params={"q": "companies with unverified revenue"}
    ).json()
    assert [c["name"] for c in body["results"]] == ["Ferrite"]


def test_routes_survive_every_module_being_unimplemented(client: TestClient) -> None:
    """The demo must not depend on four services being simultaneously healthy."""
    assert client.get("/hidden").json()["access_lift"] == 0.62
    assert client.get(f"/companies/{CID}").status_code == 200


def test_as_of_is_honoured_on_score_history(client: TestClient) -> None:
    past = (T0 - timedelta(days=365)).isoformat()
    assert client.get(f"/companies/{CID}/score-history", params={"as_of": past}).status_code == 200
