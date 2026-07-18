"""The one funnel. Owner: B. Inbound decks and outbound scanners take the same path.

normalize -> sanitize -> stamp observed_at -> emit Events. No special cases.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from schema.events import Event, EventKind, RawSignal, Source

from core.llm import wrap_untrusted
from sourcing.sanitize import sanitize as sanitize_text


def _normalize_content(raw: RawSignal) -> tuple[str, dict]:
    """Normalize raw content to text and metadata.

    Handles both string and bytes content.
    """
    if isinstance(raw.content, bytes):
        try:
            text = raw.content.decode("utf-8")
        except UnicodeDecodeError:
            # Try other encodings
            try:
                text = raw.content.decode("latin-1")
            except UnicodeDecodeError:
                text = raw.content.decode("utf-8", errors="replace")

        # Add metadata about encoding
        meta = dict(raw.meta) if raw.meta else {}
        meta["content_encoding"] = "utf-8"
        return text, meta
    elif isinstance(raw.content, str):
        return raw.content, dict(raw.meta) if raw.meta else {}
    else:
        # Serialize other types to JSON
        return json.dumps(raw.content), dict(raw.meta) if raw.meta else {}


def _extract_observables(content: str, source: Source) -> list[dict]:
    """Extract observables from content for entity resolution.

    Looks for common patterns like GitHub usernames, HN usernames, emails.
    """
    import re

    observables = []

    # GitHub usernames: @username or github.com/username
    github_pattern = r"(?:^|[\s@])([a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38})(?:$|[\s,;])"
    for match in re.finditer(github_pattern, content):
        username = match.group(1)
        if len(username) > 0 and username != "-":
            observables.append({"type": "github", "value": username.lower()})

    # HN usernames: hnuser or hackernews.com/user/username
    hn_pattern = r"\b([a-zA-Z][a-zA-Z0-9._-]{1,30})\b"
    if source == Source.HN:
        for match in re.finditer(hn_pattern, content):
            username = match.group(1)
            if not username.isdigit():  # Skip numeric IDs
                observables.append({"type": "hn", "value": username.lower()})

    # Email addresses
    email_pattern = r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b"
    for match in re.finditer(email_pattern, content):
        email = match.group(1)
        observables.append({"type": "email", "value": email.lower()})

    # URLs
    url_pattern = r"https?://[^\s<>)]+"
    for match in re.finditer(url_pattern, content):
        url = match.group(0)
        observables.append({"type": "url", "value": url.lower()})

    return observables


def _generate_event_id(raw: RawSignal, content_hash: str, kind: EventKind) -> UUID:
    """Generate a deterministic event ID based on source and content."""
    # Create a unique hash for this event
    hash_input = f"{raw.source}:{raw.source_url or ''}:{content_hash}"
    hash_bytes = hashlib.sha256(hash_input.encode()).digest()[:16]
    return UUID(bytes=hash_bytes)


def _get_observed_at(raw: RawSignal, source: Source, content: str) -> datetime:
    """Determine the observed_at timestamp from the raw signal or content.

    Priority:
    1. raw.meta["observed_at"] if present
    2. raw.fetched_at - but this is NOT observed_at, just a fallback
    3. Parse from content for certain sources
    """
    # Check if we already have observed_at in meta
    if "observed_at" in raw.meta:
        try:
            dt = raw.meta["observed_at"]
            if isinstance(dt, datetime):
                return dt
        except Exception:
            pass

    # For certain sources, we should always have a proper timestamp
    # If not, we need to flag it
    source_needs_real_timestamp = source in [Source.HN, Source.GITHUB, Source.ARXIV]

    if source_needs_real_timestamp:
        # These sources MUST have real timestamps - if we don't have one,
        # we should return a default and flag it
        return datetime(2020, 1, 1, tzinfo=timezone.utc)  # Fallback, will be flagged

    # For web scans, we might infer from date
    return datetime.now(timezone.utc)


def ingest(raw: RawSignal) -> list[Event]:
    """Ingest a raw signal and produce normalized Events.

    Pipeline:
    1. Normalize content (bytes -> string)
    2. Sanitize content (strip injection attacks)
    3. Stamp observed_at timestamp
    4. Generate Events

    Args:
        raw: RawSignal from a scanner or deck

    Returns:
        List of Event objects ready for store.append()
    """
    result_events = []

    # Step 1: Normalize content
    content, meta = _normalize_content(raw)
    raw.meta.update(meta)

    # Step 2: Sanitize content
    clean_content, integrity_events = sanitize_text(content, source_url=raw.source_url)

    # Collect integrity flags from the sanitize step
    integrity_flags = []
    integrity_flags.extend(integrity_events)

    # Step 3: Determine observed_at
    observed_at = _get_observed_at(raw, raw.source, content)

    # If the observed_at is a fallback (not from actual source), flag it
    if observed_at.year < 2024:  # Pre-2024 is a reasonable fallback threshold
        integrity_flags.append("observed_at_inferred")

    # Step 4: Generate Events based on source type

    # Content hash for ID generation
    content_hash = hashlib.sha256(clean_content.encode()).hexdigest()[:16]

    if raw.source == Source.HN:
        result_events.extend(_ingest_hn(raw, clean_content, observed_at, integrity_flags, content_hash))
    elif raw.source == Source.GITHUB:
        result_events.extend(_ingest_github(raw, clean_content, observed_at, integrity_flags, content_hash))
    elif raw.source == Source.ARXIV:
        result_events.extend(_ingest_arxiv(raw, clean_content, observed_at, integrity_flags, content_hash))
    elif raw.source == Source.WEB:
        result_events.extend(_ingest_web(raw, clean_content, observed_at, integrity_flags, content_hash))
    elif raw.source == Source.DECK:
        result_events.extend(_ingest_deck(raw, clean_content, observed_at, integrity_flags, content_hash))
    else:
        # Generic fallback
        result_events.append(Event(
            event_id=_generate_event_id(raw, content_hash, EventKind.REPO_ACTIVITY),
            kind=EventKind.REPO_ACTIVITY,
            source=raw.source,
            source_url=raw.source_url,
            observed_at=observed_at,
            payload={"content": clean_content, **raw.meta},
            evidence_span=None,
            confidence=0.8,
            integrity_flags=integrity_flags,
        ))

    # Add integrity events from sanitization
    result_events.extend(integrity_events)

    return result_events


def _ingest_hn(raw: RawSignal, content: str, observed_at: datetime, integrity_flags: list[str], content_hash: str) -> list[Event]:
    """Ingest HN raw signal."""
    result_events = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"raw": content}

    object_id = data.get("object_id", "")
    kind = EventKind.HN_POST

    # Determine if this is a comment
    is_comment = data.get("meta", {}).get("is_comment", False)
    if is_comment:
        kind = EventKind.HN_COMMENT

    # Create main event
    payload = {
        "object_id": object_id,
        "title": data.get("title", ""),
        "author": data.get("author", ""),
    }

    result_events.append(Event(
        event_id=_generate_event_id(raw, content_hash, kind),
        kind=kind,
        source=Source.HN,
        source_url=raw.source_url,
        observed_at=observed_at,
        payload=payload,
        evidence_span=None,
        confidence=0.95,
        integrity_flags=integrity_flags,
    ))

    # If there's a story_text or comment_text, create a profile fact event
    text = data.get("story_text") or data.get("comment_text") or ""
    if text:
        result_events.append(Event(
            event_id=uuid4(),
            kind=EventKind.PROFILE_FACT,
            source=Source.HN,
            source_url=raw.source_url,
            observed_at=observed_at,
            payload={"text": text, "text_type": "story" if not is_comment else "comment"},
            evidence_span=text[:200] if text else None,
            confidence=0.9,
            integrity_flags=integrity_flags,
        ))

    return result_events


def _ingest_github(raw: RawSignal, content: str, observed_at: datetime, integrity_flags: list[str], content_hash: str) -> list[Event]:
    """Ingest GitHub raw signal."""
    result_events = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"raw": content}

    repo_type = data.get("type", "")

    if repo_type == "user":
        payload = {
            "username": data.get("login", ""),
            "name": data.get("name", ""),
            "followers_count": data.get("followers_count", 0),
            "repositories_contributed_to": data.get("repositories_contributed_to", 0),
            "location": data.get("location", ""),
            "website": data.get("website", ""),
        }

        events.append(Event(
            event_id=_generate_event_id(raw, content_hash, EventKind.PROFILE_FACT),
            kind=EventKind.PROFILE_FACT,
            source=Source.GITHUB,
            source_url=raw.source_url,
            observed_at=observed_at,
            payload=payload,
            evidence_span=None,
            confidence=0.9,
            integrity_flags=integrity_flags,
        ))

    elif repo_type == "repo" or repo_type == "repo_search":
        payload = {
            "owner": data.get("owner", data.get("username", "")),
            "name": data.get("name", ""),
            "stargazers": data.get("stargazers", data.get("stargazerCount", 0)),
            "forks": data.get("forks", data.get("forkCount", 0)),
            "languages": data.get("languages", []),
            "created_at": data.get("created_at"),
        }

        result_events.append(Event(
            event_id=_generate_event_id(raw, content_hash, EventKind.REPO_ACTIVITY),
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            source_url=raw.source_url,
            observed_at=observed_at,
            payload=payload,
            evidence_span=None,
            confidence=0.9,
            integrity_flags=integrity_flags,
        ))

    elif repo_type == "commit":
        payload = {
            "oid": data.get("oid", ""),
            "author_login": data.get("author_login", ""),
            "committed_date": data.get("committed_date"),
            "message": data.get("message", ""),
        }

        result_events.append(Event(
            event_id=uuid4(),
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            source_url=raw.source_url,
            observed_at=observed_at,
            payload=payload,
            evidence_span=data.get("message"),
            confidence=0.95,
            integrity_flags=integrity_flags,
        ))

    elif repo_type == "user_contributions":
        payload = {
            "username": data.get("username", ""),
            "contributions": data.get("contributions", {}),
        }

        result_events.append(Event(
            event_id=uuid4(),
            kind=EventKind.REPO_ACTIVITY,
            source=Source.GITHUB,
            source_url=raw.source_url,
            observed_at=observed_at,
            payload=payload,
            evidence_span=None,
            confidence=0.85,
            integrity_flags=integrity_flags,
        ))

    return result_events


def _ingest_arxiv(raw: RawSignal, content: str, observed_at: datetime, integrity_flags: list[str], content_hash: str) -> list[Event]:
    """Ingest arXiv raw signal."""
    result_events = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"raw": content}

    payload = {
        "arxiv_id": data.get("arxiv_id", ""),
        "title": data.get("title", ""),
        "authors": data.get("authors", []),
        "categories": data.get("categories", []),
        "summary": data.get("summary", ""),
    }

    # Create paper event
    result_events.append(Event(
        event_id=_generate_event_id(raw, content_hash, EventKind.PAPER),
        kind=EventKind.PAPER,
        source=Source.ARXIV,
        source_url=raw.source_url,
        observed_at=observed_at,
        payload=payload,
        evidence_span=data.get("summary", "")[:500] if data.get("summary") else None,
        confidence=0.95,
        integrity_flags=integrity_flags,
    ))

    # Create profile fact events for authors
    for author in data.get("authors", []):
        author_name = author.get("name", "")
        if author_name:
            result_events.append(Event(
                event_id=uuid4(),
                kind=EventKind.PROFILE_FACT,
                source=Source.ARXIV,
                source_url=raw.source_url,
                observed_at=observed_at,
                payload={"author_name": author_name, "affiliation": author.get("affiliation", "")},
                evidence_span=author_name,
                confidence=0.9,
                integrity_flags=integrity_flags,
            ))

    return result_events


def _ingest_web(raw: RawSignal, content: str, observed_at: datetime, integrity_flags: list[str], content_hash: str) -> list[Event]:
    """Ingest web raw signal (Tavily enrichment)."""
    result_events = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"raw": content}

    source_type = data.get("source_type", "article")

    payload = {
        "url": data.get("url", ""),
        "title": data.get("title", ""),
        "source_type": source_type,
        "content_snippet": data.get("snippet", ""),
    }

    result_events.append(Event(
        event_id=_generate_event_id(raw, content_hash, EventKind.REPO_ACTIVITY),
        kind=EventKind.REPO_ACTIVITY,
        source=Source.WEB,
        source_url=raw.source_url,
        observed_at=observed_at,
        payload=payload,
        evidence_span=data.get("snippet", "")[:200] if data.get("snippet") else None,
        confidence=0.7,
        integrity_flags=integrity_flags,
    ))

    return result_events


def _ingest_deck(raw: RawSignal, content: str, observed_at: datetime, integrity_flags: list[str], content_hash: str) -> list[Event]:
    """Ingest deck raw signal (PDF extraction)."""
    result_events = []

    # For decks, content is typically extracted text with slide metadata
    # The deck module handles slide IDs, we just pass through

    payload = {
        "content": content,
        "slide_ids": raw.meta.get("slide_ids", []),
    }

    result_events.append(Event(
        event_id=_generate_event_id(raw, content_hash, EventKind.DECK_CLAIM),
        kind=EventKind.DECK_CLAIM,
        source=Source.DECK,
        source_url=raw.source_url,
        observed_at=observed_at,
        payload=payload,
        evidence_span=None,
        confidence=0.8 if "ocr_low_conf" in integrity_flags else 0.95,
        integrity_flags=integrity_flags,
    ))

    return result_events
