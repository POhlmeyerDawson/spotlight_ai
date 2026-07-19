"""Sourcing module. Owner: B.

This module handles:
- Scanners (HN, GitHub, arXiv, web) for raw signals
- Ingestion bus for normalization and sanitization
- Graph for hidden founder ranking
- Burst detection for fast builder identification
- Outreach drafting for founder activation
"""

from __future__ import annotations

from sourcing.bus import ingest
from sourcing.sanitize import sanitize, wrap_content, is_wrapped, strip_untrusted_wrapper
from sourcing.graph import hidden_ranking, access_lift
from sourcing.burst import burst_signature
from sourcing.outreach import draft, eligibility
from sourcing.deck import extract

__all__ = [
    "ingest",
    "sanitize",
    "wrap_content",
    "is_wrapped",
    "strip_untrusted_wrapper",
    "hidden_ranking",
    "access_lift",
    "burst_signature",
    "draft",
    "eligibility",
    "extract",
]
