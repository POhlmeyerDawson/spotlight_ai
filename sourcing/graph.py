"""Collaboration graph + personalized PageRank. Owner: B. See B.md H8-12.

    hidden(v) = z(ppr(v)) - z(visibility(v))

High proximity to greatness, low individual visibility = the founder nobody emailed.
Edges are observed_at-stamped and MUST be as_of-filterable — the backtest replays this.

Edges are derived from co-membership in a *group* rather than from explicit pair lists,
because an Event carries exactly one entity_id: two people are linked when both have an
event pointing at the same repo / paper / thread. An edge's observed_at is therefore the
LATER of the two sides (the collaboration is not observable until both halves exist) —
stamping it with the earlier side would leak an edge into the past and quietly invalidate
every backtest claim built on it.

Payload keys are read tolerantly: the scanners are being written in parallel, so an
unfamiliar shape is skipped, never fatal.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections import defaultdict
from datetime import datetime
from uuid import UUID

import networkx as nx
import numpy as np

from memory import store
from schema.events import EventKind, HiddenCandidate, utcnow

log = logging.getLogger(__name__)

# Breakout founders to seed personalized PageRank on. Names or handles; matched against
# entity display_name / name_normalized. D: extend this list — it is the only knob that
# decides what "proximity to greatness" points at.
SEED_FOUNDERS: list[str] = []

# Relative edge strength. Co-authorship and co-commit are deliberate collaboration;
# a shared thread is weak evidence and must not dominate the walk.
EDGE_WEIGHTS: dict[str, float] = {
    "co_commit": 1.0,
    "coauthor": 1.0,
    "fork": 0.6,
    "thread": 0.35,
}

# A 300-reply thread is not 45k collaborations. Above this, co-membership stops meaning
# anything and the clique would swamp the graph, so the group is dropped entirely.
MAX_GROUP_SIZE = 40

# access_lift: "near-zero visibility" is the bottom quartile of the visible population,
# not a magic follower count. A threshold in absolute followers ages badly across sources;
# a percentile is self-calibrating and is what the pitch number actually means —
# "N% of our picks are in the least-visible quarter of everyone we can see."
NEAR_ZERO_VISIBILITY_PCTILE = 25.0

# Fallback personalization when no seed resolves: the top decile by degree.
FALLBACK_SEED_FRAC = 0.10

_REPO_KEYS = ("repo", "repo_full_name", "full_name", "repo_name", "nameWithOwner")
_FORK_KEYS = ("forked_from", "fork_of", "parent_repo", "upstream", "parent")
_PAPER_KEYS = ("paper_id", "arxiv_id", "doi", "title")
_THREAD_KEYS = ("thread_id", "story_id", "root_id", "parent_id", "objectID", "id")
_FOLLOWER_KEYS = ("followers", "follower_count", "followers_count")
_KARMA_KEYS = ("karma", "hn_karma")
_STAR_KEYS = ("stars", "stargazers", "stargazers_count", "star_count")
_OWNER_KEYS = ("owner", "is_owner", "owned", "owns_repo")

_GRAPH_KINDS = (
    EventKind.REPO_ACTIVITY,
    EventKind.RELEASE,
    EventKind.PAPER,
    EventKind.HN_POST,
    EventKind.HN_COMMENT,
)


def _first(payload: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = payload.get(k)
        if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip():
            return str(v).strip().lower()
    return None


def _number(payload: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = payload.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _is_owner(payload: dict) -> bool:
    """Stars count toward visibility only on repos the entity owns. Unknown ownership
    means unknown stars, which per the spec is 0 — never a guess in the founder's favour."""
    if str(payload.get("role", "")).lower() == "owner":
        return True
    return any(payload.get(k) is True for k in _OWNER_KEYS)


def _norm(name: str) -> str:
    return " ".join(name.strip().lower().split())


# ---------------------------------------------------------------------------
# V and E
# ---------------------------------------------------------------------------


def _groups(as_of: datetime) -> tuple[
    dict[tuple[str, str], dict[UUID, datetime]],
    dict[UUID, dict[str, datetime]],
]:
    """(kind, key) -> {entity: first time it joined}, plus fork claims per entity."""
    members: dict[tuple[str, str], dict[UUID, datetime]] = defaultdict(dict)
    forks: dict[UUID, dict[str, datetime]] = defaultdict(dict)

    for ev in store.events(as_of=as_of):
        if ev.entity_id is None or ev.kind not in _GRAPH_KINDS:
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        keys: list[tuple[str, str]] = []

        if ev.kind in (EventKind.REPO_ACTIVITY, EventKind.RELEASE):
            if repo := _first(payload, _REPO_KEYS):
                keys.append(("co_commit", f"repo:{repo}"))
            if parent := _first(payload, _FORK_KEYS):
                prev = forks[ev.entity_id].get(parent)
                if prev is None or ev.observed_at < prev:
                    forks[ev.entity_id][parent] = ev.observed_at
        elif ev.kind is EventKind.PAPER:
            if paper := _first(payload, _PAPER_KEYS):
                keys.append(("coauthor", f"paper:{paper}"))
        else:  # HN_POST / HN_COMMENT
            if thread := _first(payload, _THREAD_KEYS):
                keys.append(("thread", f"thread:{thread}"))

        for key in keys:
            prev = members[key].get(ev.entity_id)
            if prev is None or ev.observed_at < prev:
                members[key][ev.entity_id] = ev.observed_at

    return members, forks


def _visibility(as_of: datetime) -> dict[UUID, float]:
    """log-scaled (followers + stars on owned repos + HN karma). Unknown -> 0.

    followers/karma are point-in-time facts: take the latest reading <= as_of.
    Stars are per-repo, so they sum across owned repos (latest reading of each).
    """
    latest: dict[UUID, dict[str, tuple[datetime, float]]] = defaultdict(dict)

    for ev in store.events(as_of=as_of):
        if ev.entity_id is None:
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        slots: list[tuple[str, float]] = []
        if (f := _number(payload, _FOLLOWER_KEYS)) is not None:
            slots.append(("followers", f))
        if (k := _number(payload, _KARMA_KEYS)) is not None:
            slots.append(("karma", k))
        if (s := _number(payload, _STAR_KEYS)) is not None and _is_owner(payload):
            repo = _first(payload, _REPO_KEYS) or "?"
            slots.append((f"stars:{repo}", s))
        for slot, value in slots:
            seen = latest[ev.entity_id].get(slot)
            if seen is None or ev.observed_at >= seen[0]:
                latest[ev.entity_id][slot] = (ev.observed_at, value)

    out: dict[UUID, float] = {}
    for entity_id, slots_ in latest.items():
        followers = slots_.get("followers", (as_of, 0.0))[1]
        karma = slots_.get("karma", (as_of, 0.0))[1]
        stars = sum(v for s, (_, v) in slots_.items() if s.startswith("stars:"))
        out[entity_id] = (
            math.log1p(max(followers, 0.0))
            + math.log1p(max(stars, 0.0))
            + math.log1p(max(karma, 0.0))
        )
    return out


def build_graph(as_of: datetime) -> nx.Graph:
    """V = people, E = weighted collaboration edges observable at `as_of`."""
    members, forks = _groups(as_of)
    visibility = _visibility(as_of)
    g = nx.Graph()

    # Nodes first: an entity with events but no collaborators is a real (isolated) node,
    # and it belongs in the visibility population access_lift measures against.
    for entity_id, vis in visibility.items():
        g.add_node(entity_id, visibility=vis)
    for group in members.values():
        for entity_id in group:
            g.add_node(entity_id)

    def link(a: UUID, b: UUID, kind: str, at: datetime) -> None:
        if a == b:
            return
        w = EDGE_WEIGHTS[kind]
        if g.has_edge(a, b):
            e = g[a][b]
            e["weight"] += w
            e["kinds"] = sorted(set(e["kinds"]) | {kind})
            e["observed_at"] = min(e["observed_at"], at)
        else:
            g.add_edge(a, b, weight=w, kinds=[kind], observed_at=at)

    for (kind, key), group in members.items():
        if len(group) > MAX_GROUP_SIZE:
            log.info("graph: skipping oversized group %s (%d members)", key, len(group))
            continue
        for (a, ta), (b, tb) in itertools.combinations(group.items(), 2):
            link(a, b, kind, max(ta, tb))  # observable only once BOTH sides exist

    # Fork lineage: the forker inherits an edge to everyone who worked on the parent repo.
    for forker, parents in forks.items():
        for parent, forked_at in parents.items():
            for other, ta in members.get(("co_commit", f"repo:{parent}"), {}).items():
                link(forker, other, "fork", max(ta, forked_at))

    for node in g.nodes:
        g.nodes[node].setdefault("visibility", visibility.get(node, 0.0))
    return g


# ---------------------------------------------------------------------------
# Personalized PageRank -> hidden ranking
# ---------------------------------------------------------------------------


def _resolve_seeds(g: nx.Graph) -> tuple[dict[UUID, float], str]:
    """SEED_FOUNDERS -> a full personalization vector. Falls back to top-degree."""
    wanted = {_norm(n) for n in SEED_FOUNDERS if n.strip()}
    hits: set[UUID] = set()
    if wanted:
        for row in store.all_entities():
            names = {_norm(str(row.get("display_name") or "")), _norm(str(row.get("name_normalized") or ""))}
            if names & wanted:
                entity_id = UUID(str(row["entity_id"]))
                if entity_id in g:
                    hits.add(entity_id)

    if hits:
        note = f"personalized on {len(hits)}/{len(wanted)} resolved seed founder(s)"
    else:
        n = max(1, int(len(g) * FALLBACK_SEED_FRAC))
        hits = {v for v, _ in sorted(g.degree(weight="weight"), key=lambda kv: -kv[1])[:n]}
        note = (
            f"NO SEED FOUNDER RESOLVED ({len(wanted)} configured) — fell back to the "
            f"{len(hits)} highest-degree node(s); proximity is to hubs, not to breakouts"
        )
        log.warning("graph: %s", note)

    return {v: (1.0 if v in hits else 0.0) for v in g.nodes}, note


def _z(values: np.ndarray) -> np.ndarray:
    sd = float(values.std())
    return np.zeros_like(values) if sd == 0.0 else (values - float(values.mean())) / sd


def hidden_ranking(as_of: datetime, k: int = 50) -> list[HiddenCandidate]:
    g = build_graph(as_of)
    if len(g) == 0:
        return []

    personalization, note = _resolve_seeds(g)
    log.info("graph: hidden_ranking as_of=%s over %d nodes / %d edges — %s",
             as_of, len(g), g.number_of_edges(), note)
    ppr = nx.pagerank(g, personalization=personalization, weight="weight")

    nodes = list(g.nodes)
    z_ppr = _z(np.array([ppr[v] for v in nodes]))
    z_vis = _z(np.array([g.nodes[v]["visibility"] for v in nodes]))

    ranked = [
        HiddenCandidate(
            entity_id=v,
            ppr=float(ppr[v]),
            visibility=float(g.nodes[v]["visibility"]),
            hidden_score=float(zp - zv),
        )
        for v, zp, zv in zip(nodes, z_ppr, z_vis)
    ]
    ranked.sort(key=lambda c: (-c.hidden_score, c.visibility))
    return ranked[:k]


def access_lift(picks: list[UUID], *, as_of: datetime | None = None) -> float:
    """% of picks with near-zero traditional visibility. The closing line of the pitch.

    "Near-zero" = at or below the NEAR_ZERO_VISIBILITY_PCTILE-th percentile of the
    visibility of everyone in the graph at `as_of` — a documented percentile, so the
    number stays meaningful as the corpus grows. as_of defaults to now; the backtest
    passes its replay clock so the baseline population is itself lookahead-free.
    """
    if not picks:
        return 0.0
    g = build_graph(as_of or utcnow())
    population = np.array([g.nodes[v]["visibility"] for v in g.nodes])
    if population.size == 0:
        return 0.0
    threshold = float(np.percentile(population, NEAR_ZERO_VISIBILITY_PCTILE))
    # A pick with no visibility signal at all scores 0.0 — genuinely invisible, counts.
    hits = sum(1 for p in picks if float(g.nodes[p]["visibility"] if p in g else 0.0) <= threshold)
    return hits / len(picks)
