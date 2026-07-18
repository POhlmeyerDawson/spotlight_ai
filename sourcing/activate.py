"""Outreach draft from the evidence trace. Owner: B. Cut item #3.

Generates a ~40 line draft based on the founder's actual work.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from schema.events import utcnow
from memory.store import events


def draft(entity_id: UUID) -> str:
    """Generate an outreach draft from evidence about this entity.

    The draft should be ~40 lines and reference specific work:
    - "We noticed your work on X"
    - Reference actual repositories, papers, or comments
    - Keep it authentic and specific

    Args:
        entity_id: The entity to draft for

    Returns:
        Draft text (approximately 40 lines)
    """
    # Get events for this entity
    now = utcnow()
    three_months_ago = now - timedelta(days=90)

    entity_events = events(as_of=now, entity_id=entity_id)

    if not entity_events:
        return _fallback_draft(entity_id)

    # Categorize events by type
    repo_events = [e for e in entity_events if e.kind.value in ["repo_activity", "release"]]
    paper_events = [e for e in entity_events if e.kind.value == "paper"]
    hn_events = [e for e in entity_events if e.kind.value in ["hn_post", "hn_comment"]]
    profile_events = [e for e in entity_events if e.kind.value == "profile_fact"]

    # Extract content from events
    repo_content = _extract_repo_content(repo_events)
    paper_content = _extract_paper_content(paper_events)
    hn_content = _extract_hn_content(hn_events)

    # Build the draft
    draft_parts = []

    # Opening
    draft_parts.append(f"Hi,")
    draft_parts.append("")
    draft_parts.append("We've been tracking founder activity in the AI infrastructure space")
    draft_parts.append("and wanted to reach out based on your work.")
    draft_parts.append("")

    # Reference specific work
    if repo_content:
        draft_parts.append(f"We noticed your work on {repo_content[:100]}...")
        draft_parts.append("Your contributions to that repository show strong engineering rigor.")
        draft_parts.append("")

    if paper_content:
        draft_parts.append(f"Your paper on {paper_content[:100]} was particularly interesting.")
        draft_parts.append("The technical approach aligns well with what we're seeing in the field.")
        draft_parts.append("")

    if hn_content:
        draft_parts.append(f"We also enjoyed your post on HN about {hn_content[:100]}.")
        draft_parts.append("Your insights on that topic are well-regarded in the community.")
        draft_parts.append("")

    # Middle paragraph
    draft_parts.append("We're building a system to identify promising AI infrastructure founders")
    draft_parts.append("at the earliest stage - before they're widely recognized.")
    draft_parts.append("Your work suggests you're building something significant.")
    draft_parts.append("")

    # Close
    draft_parts.append("If you're open to connecting, we'd love to learn more about what you're working on.")
    draft_parts.append("Even if it's not the right time, we'd be happy to keep in touch.")
    draft_parts.append("")
    draft_parts.append("Best,")
    draft_parts.append("The YC Brain team")
    draft_parts.append("")
    draft_parts.append("P.S. We track public signals to identify founders like you - ")
    draft_parts.append("no need to reply if this isn't the right time.")

    return "\n".join(draft_parts[:40])  # Limit to 40 lines


def _extract_repo_content(events: list) -> str:
    """Extract repo-related content from events."""
    for event in events:
        payload = getattr(event, "payload", {})
        repo_name = payload.get("name") or payload.get("repo_name")
        if repo_name:
            return repo_name

    return ""


def _extract_paper_content(events: list) -> str:
    """Extract paper-related content from events."""
    for event in events:
        payload = getattr(event, "payload", {})
        title = payload.get("title")
        if title:
            return title

    return ""


def _extract_hn_content(events: list) -> str:
    """Extract HN post/comment content from events."""
    for event in events:
        payload = getattr(event, "payload", {})
        title = payload.get("title")
        text = payload.get("story_text") or payload.get("comment_text")
        if title:
            return title
        if text and len(text) > 10:
            return text[:100]

    return ""


def _fallback_draft(entity_id: UUID) -> str:
    """Generate a fallback draft when there's no specific data."""
    return f"""Hi,

We track AI infrastructure founders through public signals and wanted to reach out.
While we don't have specific work details for you yet, we're always interested in
connecting with talented builders in this space.

Our system helps identify promising founders early - before they're widely recognized.
If you're building something in AI infrastructure, we'd love to learn more.

Even if this isn't the right time, we'd be happy to keep in touch.

Best,
The YC Brain team

P.S. We track GitHub, HN, arXiv, and other public signals to find founders like you.
"""


def generate_drafts(entity_ids: list[UUID]) -> dict[UUID, str]:
    """Generate outreach drafts for multiple entities.

    Args:
        entity_ids: List of entity IDs to draft for

    Returns:
        Dict mapping entity_id to draft text
    """
    return {entity_id: draft(entity_id) for entity_id in entity_ids}


def draft_with_context(entity_id: UUID, context: str) -> str:
    """Generate an outreach draft with additional context.

    Args:
        entity_id: The entity to draft for
        context: Additional context to include (e.g., specific fund, partner name)

    Returns:
        Draft text with context incorporated
    """
    base_draft = draft(entity_id)

    # Insert context near the beginning
    lines = base_draft.split("\n")

    if len(lines) > 5:
        # Insert context after the opening
        context_line = f"We're reaching out specifically regarding {context}."
        lines.insert(5, context_line)
        lines.insert(6, "")

    return "\n".join(lines[:40])


