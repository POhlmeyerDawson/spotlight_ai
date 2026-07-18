"""Collaboration graph + personalized PageRank. Owner: B. See B.md H8-12.

    hidden(v) = z(ppr(v)) - z(visibility(v))

High proximity to greatness, low individual visibility = the founder nobody emailed.
Edges are observed_at-stamped and MUST be as_of-filterable — the backtest replays this.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import networkx as nx
import numpy as np
from scipy import stats
from schema.events import Event, EventKind, HiddenCandidate

from core.config import settings
from memory.resolver import resolve
from memory.store import append, events as store_events


# Global graph cache
_graph_cache: dict[str, Any] = {"graph": None, "as_of": None}


def _build_graph(as_of: datetime) -> nx.Graph:
    """Build the collaboration graph filtered by as_of timestamp.

    V = people (HN ∪ GitHub ∪ arXiv), resolved through A's resolver
    E = co-commit | fork lineage | co-authorship | same-thread reply
       each edge weighted and observed_at-stamped
    """
    # Check cache
    cache_key = str(as_of.timestamp())
    if _graph_cache["graph"] is not None and _graph_cache["as_of"] == as_of:
        return _graph_cache["graph"]

    G = nx.Graph()

    # Get all events up to as_of
    all_events = store_events(as_of=as_of)

    # Track seen entities and their relationships
    author_commits: dict[UUID, list[datetime]] = {}  # author -> list of commit dates
    co_authors: dict[tuple[UUID, UUID], list[datetime]] = {}  # pair -> list of co-author dates
    thread_members: dict[str, set[UUID]] = {}  # thread_id -> set of members

    for event in all_events:
        if event.entity_id is None:
            continue

        kind = event.kind

        # Handle REPO_ACTIVITY (GitHub commits)
        if kind == EventKind.REPO_ACTIVITY:
            payload = event.payload
            if "author_login" in payload or "author" in payload:
                author = event.entity_id
                if author not in author_commits:
                    author_commits[author] = []
                author_commits[author].append(event.observed_at)

            # Fork lineage: if payload has fork info
            if payload.get("forks"):
                for fork in payload["forks"]:
                    fork_entity = _resolve_handle(fork, event.observed_at)
                    if fork_entity:
                        G.add_edge(event.entity_id, fork_entity, weight=1.0, observed_at=event.observed_at)

        # Handle PAPER (arXiv co-authorship)
        elif kind == EventKind.PAPER:
            payload = event.payload
            authors = payload.get("authors", [])
            if isinstance(authors, list) and len(authors) > 1:
                # Get or create UUIDs for each author
                author_entities = []
                for author in authors:
                    author_name = author.get("name", "") if isinstance(author, dict) else author
                    if author_name:
                        author_entity = _resolve_author(author_name, event.observed_at)
                        if author_entity:
                            author_entities.append(author_entity)
                            G.add_node(author_entity, visibility=0.0)  # Initialize

                # Add co-author edges
                for i in range(len(author_entities)):
                    for j in range(i + 1, len(author_entities)):
                        pair = tuple(sorted([author_entities[i], author_entities[j]]))
                        if pair not in co_authors:
                            co_authors[pair] = []
                        co_authors[pair].append(event.observed_at)

        # Handle HN_POST and HN_COMMENT (same-thread)
        elif kind in (EventKind.HN_POST, EventKind.HN_COMMENT):
            payload = event.payload
            thread_id = payload.get("object_id", "")

            # Get participants (author and commenters)
            participants = set()
            participants.add(event.entity_id)

            if payload.get("author"):
                author_entity = _resolve_handle(payload["author"], event.observed_at)
                if author_entity:
                    participants.add(author_entity)

            # Add thread members
            if thread_id not in thread_members:
                thread_members[thread_id] = set()
            thread_members[thread_id].update(participants)

    # Add co-author edges with weights based on frequency
    for (author1, author2), dates in co_authors.items():
        if author1 in G and author2 in G:
            weight = len(dates)  # More collaborations = stronger edge
            G.add_edge(author1, author2, weight=weight, observed_at=min(dates))

    # Add thread edges
    for thread_id, members in thread_members.items():
        members_list = list(members)
        for i in range(len(members_list)):
            for j in range(i + 1, len(members_list)):
                if members_list[i] != members_list[j]:
                    G.add_edge(members_list[i], members_list[j], weight=1.0, observed_at=as_of)

    # Add fork lineage edges from events
    for event in all_events:
        if event.kind == EventKind.REPO_ACTIVITY:
            payload = event.payload
            if payload.get("forks"):
                forks = payload["forks"]
                if isinstance(forks, list):
                    for fork in forks:
                        fork_entity = _resolve_handle(fork, event.observed_at)
                        if fork_entity:
                            G.add_edge(event.entity_id, fork_entity, weight=1.0, observed_at=event.observed_at)

    # Compute visibility scores for each node
    for node in G.nodes():
        G.nodes[node]["visibility"] = _compute_visibility(node, as_of)

    # Cache the graph
    _graph_cache["graph"] = G
    _graph_cache["as_of"] = as_of

    return G


def _resolve_handle(handle: str, as_of: datetime) -> UUID | None:
    """Resolve a handle (GitHub username, HN username) to an entity_id."""
    from schema.events import EntityCandidate, Source

    candidate = EntityCandidate(
        handles={"github": handle, "hn": handle},
        source=Source.GITHUB if handle else Source.HN,
    )

    try:
        resolution = resolve(candidate)
        return resolution.entity_id
    except Exception:
        return None


def _resolve_author(name: str, as_of: datetime) -> UUID | None:
    """Resolve an author name to an entity_id."""
    from schema.events import EntityCandidate, Source

    candidate = EntityCandidate(
        name=name,
        source=Source.ARXIV,
    )

    try:
        resolution = resolve(candidate)
        return resolution.entity_id
    except Exception:
        return None


def _compute_visibility(entity_id: UUID, as_of: datetime) -> float:
    """Compute visibility score for an entity.

    visibility = followers + stars-on-owned-repos + HN karma, log-scaled
    """
    score = 0.0

    # Get followers from GitHub profile
    gh_events = events(as_of=as_of, entity_id=entity_id, kind="repo_activity")
    for event in gh_events:
        payload = event.payload
        score += payload.get("followers_count", 0)

    # Get stars on repos
    for event in gh_events:
        payload = event.payload
        score += payload.get("stargazers", 0)

    # Get HN karma (not directly available, use post/comment counts as proxy)
    hn_events = events(as_of=as_of, entity_id=entity_id, kind="hn_post")
    score += len(hn_events) * 10  # Each post worth ~10 karma

    # Log scale
    return np.log1p(max(score, 1))


def hidden_ranking(as_of: datetime, k: int = 50) -> list[HiddenCandidate]:
    """Compute hidden ranking for founders.

    hidden(v) = z(ppr_score(v)) - z(visibility(v))

    High proximity to greatness, low individual visibility = the pre-signal founder
    nobody has emailed yet. That's the pitch.

    Args:
        as_of: Filter edges to those with observed_at <= as_of
        k: Number of candidates to return

    Returns:
        List of HiddenCandidate sorted by hidden_score (descending)
    """
    # Build graph
    G = _build_graph(as_of)

    if G.number_of_nodes() == 0:
        return []

    # Identify breakout founders (seeds for personalized PageRank)
    # These are founders who are already well-connected
    seeds = _identify_seeds(G, as_of)

    if not seeds:
        # Fall back to most connected nodes as seeds
        degrees = dict(G.degree())
        seeds = [node for node, _ in sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:10]]

    # Compute Personalized PageRank
    # Using networkx's pagerank with personalization
    try:
        ppr_scores = nx.pagerank(G, personalization={node: 1.0 for node in seeds}, alpha=0.85)
    except Exception:
        # Fall back to regular pagerank
        ppr_scores = nx.pagerank(G)

    # Compute visibility scores
    visibility_scores = {node: G.nodes[node].get("visibility", 0) for node in G.nodes()}

    # Compute hidden scores
    candidates = []
    for node in G.nodes():
        ppr = ppr_scores.get(node, 0)
        visibility = visibility_scores.get(node, 0)

        # Z-score normalization
        if ppr > 0 and visibility > 0:
            ppr_z = stats.zscore([ppr, 0.1])[0]  # Avoid division by zero
            visibility_z = stats.zscore([visibility, 1])[0]
            hidden_score = ppr_z - visibility_z
        else:
            hidden_score = ppr - np.log1p(visibility)

        candidates.append(HiddenCandidate(
            entity_id=node,
            ppr=ppr,
            visibility=visibility,
            hidden_score=hidden_score,
        ))

    # Sort by hidden_score descending and return top k
    candidates.sort(key=lambda c: c.hidden_score, reverse=True)

    return candidates[:k]


def _identify_seeds(G: nx.Graph, as_of: datetime) -> list[UUID]:
    """Identify breakout founders to use as PPR seeds.

    Seeds are entities with high visibility in the graph.
    """
    # Get events for breakout signals
    breakout_events = events(as_of=as_of, kind="green_flag")

    seeds = []
    for event in breakout_events:
        if event.entity_id and event.entity_id in G:
            seeds.append(event.entity_id)

    # If no breakout events, use top-degree nodes
    if not seeds:
        degrees = dict(G.degree())
        sorted_nodes = sorted(degrees.items(), key=lambda x: x[1], reverse=True)
        seeds = [node for node, _ in sorted_nodes[:10]]

    return seeds


def access_lift(picks: list[UUID]) -> float:
    """Compute % of top-K with near-zero traditional visibility.

    This is the closing line of the pitch: the percentage of our top picks
    who have minimal traditional visibility (followers, stars, karma).

    Args:
        picks: List of entity_ids to check

    Returns:
        Float between 0 and 1 representing the percentage with near-zero visibility
    """
    if not picks:
        return 0.0

    # Compute visibility for each pick
    zero_visibility_count = 0
    total = len(picks)

    for entity_id in picks:
        # Build current graph to get visibility
        G = _build_graph(datetime.now())

        if entity_id in G:
            visibility = G.nodes[entity_id].get("visibility", 0)

            # Near-zero is defined as log(1) = 0 or very low
            if visibility < 1.0:  # Very low visibility
                zero_visibility_count += 1
        else:
            # If not in graph, assume low visibility (invisible founder)
            zero_visibility_count += 1

    return zero_visibility_count / total


def get_graph_info(as_of: datetime) -> dict[str, Any]:
    """Get summary information about the graph."""
    G = _build_graph(as_of)

    return {
        "num_nodes": G.number_of_nodes(),
        "num_edges": G.number_of_edges(),
        "avg_degree": sum(dict(G.degree()).values()) / max(G.number_of_nodes(), 1),
        "density": nx.density(G),
    }


def find_hidden_founders(as_of: datetime, k: int = 20) -> list[dict[str, Any]]:
    """Find hidden founders with detailed information.

    Returns detailed info about each hidden founder for inspection.
    """
    candidates = hidden_ranking(as_of, k)

    results = []
    for candidate in candidates:
        # Get event history for this entity
        entity_events = events(as_of=as_of, entity_id=candidate.entity_id)

        results.append({
            "entity_id": candidate.entity_id,
            "hidden_score": candidate.hidden_score,
            "ppr": candidate.ppr,
            "visibility": candidate.visibility,
            "event_count": len(entity_events),
            "event_kinds": list(set(e.kind for e in entity_events)),
        })

    return results
