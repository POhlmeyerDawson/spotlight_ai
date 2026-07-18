"""The graph is the differentiator, so these are the tests that protect the claim:
edges never leak from the future, and low visibility next to greatness ranks first."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from memory import db, store
from schema.events import Event, EventKind, Source
from sourcing import burst, graph

T1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=30)
T3 = T1 + timedelta(days=60)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VCBRAIN_DB_PATH", str(tmp_path / "test.db"))
    db.reset_connections()
    yield
    db.reset_connections()


def _repo(entity_id: UUID, repo: str, at: datetime, **payload) -> None:
    store.append(
        Event(
            entity_id=entity_id, kind=EventKind.REPO_ACTIVITY, source=Source.GITHUB,
            observed_at=at, payload={"repo": repo, **payload},
        )
    )


def _entity(name: str) -> UUID:
    return store.upsert_entity(name, name.lower())


# ---------------------------------------------------------------------------
# as_of discipline
# ---------------------------------------------------------------------------


def test_edge_from_the_future_does_not_appear() -> None:
    a, b = _entity("ada"), _entity("linus")
    _repo(a, "org/kernel", T1)
    _repo(b, "org/kernel", T3)  # the collaboration only becomes observable at T3

    assert not graph.build_graph(T2).has_edge(a, b)
    g3 = graph.build_graph(T3)
    assert g3.has_edge(a, b)
    assert g3[a][b]["observed_at"] == T3  # stamped with the LATER side, not the earlier


def test_edge_kinds_and_weights_accumulate() -> None:
    a, b, c = _entity("a"), _entity("b"), _entity("c")
    _repo(a, "org/one", T1)
    _repo(b, "org/one", T1)
    for e in (a, b):  # same paper -> co-authorship on top of the co-commit edge
        store.append(Event(entity_id=e, kind=EventKind.PAPER, source=Source.ARXIV,
                           observed_at=T1, payload={"arxiv_id": "2401.00001"}))
    _repo(c, "fork/one", T2, forked_from="org/one")  # fork lineage
    store.append(Event(entity_id=a, kind=EventKind.HN_COMMENT, source=Source.HN,
                       observed_at=T1, payload={"story_id": 99}))
    store.append(Event(entity_id=c, kind=EventKind.HN_COMMENT, source=Source.HN,
                       observed_at=T1, payload={"story_id": 99}))

    g = graph.build_graph(T3)
    assert set(g[a][b]["kinds"]) == {"co_commit", "coauthor"}
    assert g[a][b]["weight"] == pytest.approx(
        graph.EDGE_WEIGHTS["co_commit"] + graph.EDGE_WEIGHTS["coauthor"]
    )
    assert "fork" in g[c][a]["kinds"] and "thread" in g[c][a]["kinds"]
    assert g[c][b]["kinds"] == ["fork"]


def test_oversized_thread_is_not_a_clique(monkeypatch) -> None:
    monkeypatch.setattr(graph, "MAX_GROUP_SIZE", 3)
    people = [_entity(f"p{i}") for i in range(5)]
    for p in people:
        store.append(Event(entity_id=p, kind=EventKind.HN_COMMENT, source=Source.HN,
                           observed_at=T1, payload={"story_id": 1}))
    assert graph.build_graph(T2).number_of_edges() == 0


# ---------------------------------------------------------------------------
# the pitch: low visibility next to greatness
# ---------------------------------------------------------------------------


def _seeded_world() -> tuple[UUID, UUID, UUID]:
    """Two people equally adjacent to the seed; one famous, one invisible."""
    seed = _entity("breakout founder")
    hidden = _entity("hidden one")
    famous = _entity("famous one")

    for peer in (hidden, famous):
        _repo(seed, "seed/core", T1)
        _repo(peer, "seed/core", T1)
    # identical structure, opposite visibility
    _repo(famous, "famous/proj", T1, owner=True, stars=40_000, followers=25_000)
    store.append(Event(entity_id=famous, kind=EventKind.HN_POST, source=Source.HN,
                       observed_at=T1, payload={"story_id": 7, "karma": 30_000}))
    _repo(hidden, "hidden/proj", T1, owner=True, stars=3)
    return seed, hidden, famous


def test_hidden_ranking_puts_the_invisible_peer_above_the_famous_one(monkeypatch, caplog) -> None:
    seed, hidden, famous = _seeded_world()
    monkeypatch.setattr(graph, "SEED_FOUNDERS", ["Breakout Founder"])  # match is normalized

    with caplog.at_level("WARNING"):
        ranked = graph.hidden_ranking(T2, k=10)
    assert "NO SEED FOUNDER RESOLVED" not in caplog.text  # the seed really resolved
    order = [c.entity_id for c in ranked]
    assert order.index(hidden) < order.index(famous)

    by_id = {c.entity_id: c for c in ranked}
    assert by_id[hidden].ppr == pytest.approx(by_id[famous].ppr, rel=0.05)  # equal proximity
    assert by_id[hidden].visibility < by_id[famous].visibility  # opposite visibility
    assert by_id[hidden].hidden_score > by_id[famous].hidden_score


def test_unresolved_seeds_fall_back_to_degree(monkeypatch, caplog) -> None:
    _seeded_world()
    monkeypatch.setattr(graph, "SEED_FOUNDERS", ["nobody by that name"])
    with caplog.at_level("WARNING"):
        assert graph.hidden_ranking(T2, k=5)
    assert "NO SEED FOUNDER RESOLVED" in caplog.text


def test_visibility_ignores_stars_on_repos_the_entity_does_not_own() -> None:
    contributor = _entity("contributor")
    _repo(contributor, "someone/else", T1, stars=90_000)  # no ownership marker
    assert graph.build_graph(T2).nodes[contributor]["visibility"] == 0.0


def test_hidden_ranking_is_empty_on_an_empty_world() -> None:
    assert graph.hidden_ranking(T2) == []


# ---------------------------------------------------------------------------
# access_lift
# ---------------------------------------------------------------------------


def test_access_lift_is_a_sane_fraction() -> None:
    people = [_entity(f"v{i}") for i in range(8)]
    for i, p in enumerate(people):
        _repo(p, f"p{i}/repo", T1, owner=True, stars=10**i)  # visibility spread

    invisible, visible = people[:2], people[-2:]
    assert graph.access_lift(invisible, as_of=T2) == 1.0
    assert graph.access_lift(visible, as_of=T2) == 0.0
    assert graph.access_lift(invisible + visible, as_of=T2) == pytest.approx(0.5)
    assert graph.access_lift([], as_of=T2) == 0.0
    assert graph.access_lift([uuid4()], as_of=T2) == 1.0  # unknown = invisible


# ---------------------------------------------------------------------------
# burst: substance, not volume
# ---------------------------------------------------------------------------


def _commit(day: int, files: list[str], lines: int, message: str = "work") -> dict:
    return {
        "files": files, "additions": lines, "deletions": 0, "message": message,
        "authored_at": (T1 + timedelta(days=day)).isoformat(),
    }


def _push(entity_id: UUID, commits: list[dict]) -> None:
    store.append(Event(entity_id=entity_id, kind=EventKind.COMMIT_BURST, source=Source.GITHUB,
                       observed_at=T2, payload={"repo": "x/y", "commits": commits}))


def test_substantive_high_volume_committer_is_not_flagged() -> None:
    e = _entity("real builder")
    commits = [_commit(0, [f"src/mod_{i}.py", f"tests/test_mod_{i}.py"], 40 + i * 7)
               for i in range(30)]
    commits += [_commit(d, [f"src/late_{d}.py"], 30) for d in range(1, 5)]
    _push(e, commits)

    sig = burst.burst_signature(e, as_of=T3)
    assert sig["burst"] is True  # it IS a spike, and we say so
    assert sig["substance"] >= burst.SUBSTANCE_FLOOR
    assert sig["suspicious"] is False


def test_high_volume_no_substance_is_flagged() -> None:
    e = _entity("padder")
    # same file, one-line whitespace churn, no tests
    _push(e, [_commit(0, ["README.md"], 1, "reformat whitespace") for _ in range(40)]
          + [_commit(d, ["README.md"], 1, "typo") for d in range(1, 5)])

    sig = burst.burst_signature(e, as_of=T3)
    assert sig["burst"] is True
    assert sig["substance"] < burst.SUBSTANCE_FLOOR
    assert sig["suspicious"] is True
    assert sig["logic_ratio"] == 0.0


def test_burst_signature_without_evidence_is_not_suspicious() -> None:
    sig = burst.burst_signature(_entity("ghost"), as_of=T3)
    assert sig["commits"] == 0 and sig["suspicious"] is False
