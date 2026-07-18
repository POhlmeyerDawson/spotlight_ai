"""Memory module. Owner: A.

This module handles:
- Event store (append-only)
- Entity resolution
- Founder scoring
"""

from __future__ import annotations

from memory.store import append, events, clear, count, get_event
from memory.resolver import resolve, resolve_batch, find_potential_matches
from memory.score import founder, forecast

__all__ = [
    "append",
    "events",
    "clear",
    "count",
    "get_event",
    "resolve",
    "resolve_batch",
    "find_potential_matches",
    "founder",
    "forecast",
]
