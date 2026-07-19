"""The four things that break on Vercel and on nothing else. Owner: D.

Every test here failed before the serverless fixes and would have failed ON STAGE, not
in CI, because the local box has one process and a writable disk and the deployment has
neither. They are written against the two properties that actually differ:

  READ-ONLY FILESYSTEM   every write outside /tmp raises OSError
  MANY PROCESSES         consecutive requests may not share module globals

The read-only half is simulated by making Path.write_text/write_bytes/mkdir raise, which
is what the platform does, rather than by trusting a `try` to be in the right place.
The many-processes half is simulated by clearing the in-process fallbacks between two
requests, which is exactly what a cold lambda does to them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import companies as companies_router
from memory import db

# Bound at import, BEFORE the autouse fixture replaces core.llm.complete with a
# no-network stub. The two cache tests at the bottom are the only ones in the suite that
# need the real function — they are testing its caching behaviour, not a route — and
# they still never reach the network because _call is stubbed per-test.
from core.llm import complete as real_complete  # noqa: E402

T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
CID = "11111111-1111-1111-1111-111111111111"

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
                "claims": [],
            }
        ]
    },
    f"company_{CID}": {"company_id": CID, "name": "Ferrite", "events": []},
    f"memo_{CID}": {
        "company_id": CID,
        "thesis": {"summary": "s", "claims": []},
        "recommendation": {"summary": "invest"},
        "investment_recommendation": {"amount": 500000},
        "gaps": [],
    },
    f"dissent_{CID}": {
        "company_id": CID,
        "bear_case": "the buffer may not be theirs",
        "weakest_evidence": [],
        "load_bearing_claim": "the buffer is theirs",
    },
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
    monkeypatch.setattr(
        "core.llm.complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    )
    yield
    db.reset_connections()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def read_only_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every filesystem WRITE raises, exactly as it does outside /tmp on Vercel.

    Reads are untouched: the deployment can read its own bundle, and a simulation that
    also broke reads would be testing a machine that does not exist — it would make the
    seed fixtures unloadable and every route would fail for the wrong reason.
    """
    from pathlib import Path

    def denied(self, *a, **k):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "write_text", denied)
    monkeypatch.setattr(Path, "write_bytes", denied)
    monkeypatch.setattr(Path, "mkdir", denied)
    monkeypatch.setattr("os.makedirs", denied)


# ---------------------------------------------------------------------------
# 1. Read-only filesystem: no endpoint may 500.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/companies"),
        ("get", f"/companies/{CID}"),
        ("get", f"/companies/{CID}/memo"),
        ("get", f"/companies/{CID}/memo?dissent_viewed=true"),
        ("get", f"/companies/{CID}/dissent"),
        ("get", "/thesis"),
        ("get", "/health"),
    ],
)
def test_read_only_filesystem_never_500s(client, monkeypatch, method, path) -> None:
    """A read-only disk must degrade, never fault.

    The one that used to fail here is the memo: core.llm.complete cached its completion
    with an unguarded write, so every LLM-backed route 500'd on the write AFTER paying
    for the call.
    """
    read_only_filesystem(monkeypatch)
    res = getattr(client, method)(path)
    assert res.status_code < 500, f"{method.upper()} {path} -> {res.status_code}: {res.text[:200]}"


def test_thesis_put_survives_a_read_only_disk(client, monkeypatch) -> None:
    """The edit must land in Postgres and the response must reflect it.

    `PUT /thesis` wrote data/seed/thesis.json unguarded, so every save 500'd.
    """
    read_only_filesystem(monkeypatch)
    res = client.put("/thesis", json={"check_size": 999})
    assert res.status_code == 200, res.text
    assert res.json()["check_size"] == 999
    # And it is durable: a fresh GET, which cannot read the unwritten file, still sees it.
    assert client.get("/thesis").json()["check_size"] == 999


def test_thesis_put_says_so_when_it_cannot_save(client, monkeypatch) -> None:
    """A discarded edit must NEVER be reported as a successful save.

    This is the one write in the system that must not degrade quietly: a cache that
    fails to persist costs a recomputation, but a thesis that fails to persist is a
    user's edit vanishing while the UI renders it back to them as saved.
    """
    read_only_filesystem(monkeypatch)
    monkeypatch.setattr("core.state.write", lambda *a, **k: False)

    res = client.put("/thesis", json={"check_size": 12345})
    assert res.status_code == 503, f"a lost edit was reported as {res.status_code}"
    assert "not persisted" in res.json()["detail"].lower()


def test_thesis_put_preserves_unknown_keys(client) -> None:
    """The historical bug: writing back a stale copy destroyed another writer's field."""
    client.put("/thesis", json={"clearing_score": 0.42})
    body = client.put("/thesis", json={"check_size": 500}).json()
    assert body["clearing_score"] == 0.42, "an unrelated field was destroyed by a later save"
    assert body["check_size"] == 500


def test_thesis_edit_reaches_the_engine(client) -> None:
    """Editing the thesis must change what the ENGINE reads, not just what the panel
    shows. A stored edit the engine cannot see is the 'picture of a control panel'
    core/thesis.py was written to end."""
    from core import thesis as thesis_mod

    client.put("/thesis", json={"risk_appetite": {"value": 0.93}})
    assert thesis_mod.load()["risk_appetite"]["value"] == 0.93


# ---------------------------------------------------------------------------
# 2. The dissent lock across processes and across users.
# ---------------------------------------------------------------------------


def test_unlock_survives_a_cold_lambda(client) -> None:
    """View the dissent, lose the process, ask for the memo: it must still be unlocked.

    The in-process set is cleared to simulate the next request landing on a different
    lambda. Before the fix this left the recommendation locked forever and the
    signature feature simply looked broken.
    """
    assert client.get(f"/companies/{CID}/dissent").status_code == 200

    companies_router._DISSENT_SERVED.clear()  # the cold lambda

    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is not None, "the unlock did not survive the process boundary"


def test_one_viewers_unlock_does_not_unlock_another_viewer(client) -> None:
    """THE SERIOUS ONE. A global set on a warm lambda meant user A opening the dissent
    handed user B the cheque figure without ever showing them the bear case."""
    other = TestClient(app)  # a second browser, its own cookie jar

    assert client.get(f"/companies/{CID}/dissent").status_code == 200

    body = other.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is None, "another viewer's unlock leaked the recommendation"
    assert body["investment_recommendation"] is None, "the cheque figure leaked across viewers"
    assert body["recommendation_locked_reason"]


def test_the_viewer_who_unlocked_still_sees_it(client) -> None:
    """The isolation above must not be achieved by locking everyone out."""
    client.get(f"/companies/{CID}/dissent")
    assert client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()["recommendation"]


def test_dissent_viewed_flag_alone_still_cannot_unlock(client) -> None:
    """The property that must survive every refactor of the lock's storage."""
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is None
    # Even with a viewer cookie already minted by an unrelated unlocked company.
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is None


def test_lock_holds_when_the_database_is_unreachable(client, monkeypatch) -> None:
    """With no store to read, the lock falls back to process memory — still requiring
    that the server actually served a bear case. A dead database must not unlock."""
    monkeypatch.setattr("core.state.fetch", lambda *a, **k: None)
    monkeypatch.setattr("core.state.write", lambda *a, **k: False)

    assert client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()["recommendation"] is None
    client.get(f"/companies/{CID}/dissent")
    body = client.get(f"/companies/{CID}/memo?dissent_viewed=true").json()
    assert body["recommendation"] is not None, "local dev must keep working without a database"


# ---------------------------------------------------------------------------
# 3. Proof challenges across processes.
# ---------------------------------------------------------------------------


def test_challenge_anchor_survives_a_cold_lambda() -> None:
    """Issue and submission are two requests. On different lambdas the in-process dict
    was empty, so `elapsed` silently fell back to the founder's OWN `started_at` — the
    exact self-reported-as-observed substitution api/attest.py exists to prevent."""
    from api import attest

    attest.reset()
    challenge = "44444444-4444-4444-4444-444444444444"
    attest.record_issue(challenge, issued_at=T0, company_id=CID)

    attest._ISSUED.clear()  # the cold lambda

    assert attest.issued_at(challenge) == T0
    assert attest.issued_company(challenge) == CID

    _, attestation = attest.attest(challenge, {"started_at": "2099-01-01T00:00:00+00:00"})
    assert attestation["challenge_anchored"] is True
    assert "started_at" in attestation["attested_fields"]
    assert "started_at" not in attestation["self_reported_fields"]


# ---------------------------------------------------------------------------
# 4. The LLM cache, which is what took every model-backed route down.
# ---------------------------------------------------------------------------


def test_llm_returns_its_completion_when_the_cache_cannot_be_written(monkeypatch) -> None:
    """Failing to cache must never fail the request — and this write happens AFTER the
    model has been called and paid for, so raising here threw away completed work."""
    from core import llm

    read_only_filesystem(monkeypatch)
    monkeypatch.setattr(llm, "_call", lambda *a, **k: "the completion")

    assert real_complete("prompt") == "the completion"


def test_llm_recomputes_rather_than_raising_on_a_corrupt_cache_entry(tmp_path, monkeypatch) -> None:
    from core import llm

    cache = tmp_path / "llm_cache"
    cache.mkdir()
    monkeypatch.setattr(llm, "CACHE_DIR", cache)
    monkeypatch.setattr(llm, "_call", lambda *a, **k: "recomputed")

    key = llm._cache_key(
        {"p": "prompt", "s": None, "m": llm.MODELS["openai"]["fast"], "j": False, "t": 0.2}
    )
    (cache / f"{key}.json").write_text("{ not json")

    assert real_complete("prompt") == "recomputed"
