"""Injection guard. Owner: B. The Type 5 demo beat.

STRIP, don't reject — and log an INTEGRITY event quoting the offending span.
The trace showing the caught injection IS the demo.

Rule order matters: invisible characters go first, because "i<ZWSP>gnore previous
instructions" defeats every text rule below until the ZWSP is gone.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from schema.events import Event, EventKind, Source, utcnow

STRIPPED_FLAG = "injection_stripped"

# Zero-width, bidi-override and other characters that render as nothing but carry payload.
INVISIBLE_CHARS = "​‌‍‎‏‪‫‬‭‮⁠⁡⁢⁣⁤⁦⁧⁨⁩﻿­᠎؜"


@dataclass(frozen=True)
class Rule:
    name: str
    description: str  # D renders these as "what we check for"
    pattern: re.Pattern[str] | None = None  # None -> handled by a dedicated pass
    join: str = " "  # what replaces the stripped span


RULES: list[Rule] = [
    Rule(
        "invisible_unicode",
        "Zero-width, soft-hyphen and bidi-override characters hiding text from a human reader.",
        re.compile(f"[{INVISIBLE_CHARS}]+"),
        join="",
    ),
    Rule(
        "encoded_blob",
        "Long base64-looking blob — a common way to smuggle instructions past a text filter.",
        re.compile(
            r"\b(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*[0-9])"
            r"[A-Za-z0-9+/]{48,}={0,2}"
        ),
    ),
    Rule(
        "instruction_override",
        "Telling the model to ignore, disregard or override what it was already told.",
        re.compile(
            r"(?:ignore|disregard|forget|override|bypass|skip)\b[^.\n]{0,40}?"
            r"\b(?:previous|prior|preceding|earlier|above|foregoing|all|any)\b[^.\n]{0,40}?"
            r"\b(?:instruction|prompt|rule|direction|guideline|constraint|context|message)s?\b"
            r"[^.\n]{0,60}",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "role_reassignment",
        "Reassigning the model's identity or role mid-document.",
        re.compile(
            r"(?:you are now|you're now|from now on,? you|pretend (?:to be|you are|that you)|"
            r"roleplay as|assume the role of|act as if you|you must now|your new (?:role|task) is)"
            r"[^.\n]{0,120}",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "role_token",
        "Chat role prefixes (system:/assistant:/user:) faking a conversation turn.",
        re.compile(r"(?im)^[ \t]*(?:system|assistant|user|human|ai)[ \t]*:[^\n]*"),
    ),
    Rule(
        "chat_control_token",
        "Model control tokens (<|im_start|>, [INST], <<SYS>>) pasted into the document body.",
        re.compile(
            r"(?:<\|[a-z_]+\|>|\[/?INST\]|<</?SYS>>|###\s*(?:system|instruction)s?\b[^\n]*)",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "new_instructions",
        "Announcing a fresh set of instructions or a replacement system prompt.",
        re.compile(
            r"(?:(?:new|updated|revised|additional|important|urgent|real)\s+"
            r"(?:system\s+)?(?:instruction|directive|prompt|rule)s?|system\s+prompt)\b[^\n]{0,120}",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "output_hijack",
        "Dictating what the model must output, or what it must conceal.",
        re.compile(
            r"(?:do not (?:mention|reveal|report|output|include|flag)|"
            r"reply only with|respond only with|output only|print the following|say exactly|"
            r"instead,? (?:output|reply|respond|say|write))[^\n]{0,120}",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "score_manipulation",
        "Asking the evaluator directly for a favourable score or recommendation.",
        re.compile(
            r"(?:rate|score|rank|grade|classify|recommend)\b[^.\n]{0,60}?"
            r"(?:highest|maximum|10\s*/\s*10|100\s*%|top\s+\d*|best|strong\s+(?:buy|yes)|"
            r"immediate\s+invest\w*|as\s+(?:exceptional|outstanding))[^.\n]{0,60}",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "keyword_stuffing",
        "The same keyword repeated far past natural use, to bias retrieval or scoring.",
        None,  # frequency pass, not a single regex
    ),
]

RULES_BY_NAME = {r.name: r for r in RULES}

# 5+ back-to-back repeats of the same word: "inference inference inference inference inference"
_RUN_RE = re.compile(r"\b(\w{3,})\b(?:[\s,;/|·•\-]+\1\b){4,}", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]{2,}")

_STUFF_MIN_TOKENS = 20
_STUFF_MIN_COUNT = 8
_STUFF_MIN_SHARE = 0.18
_STUFF_KEEP = 3  # occurrences left in place; the rest are stripped


def sanitize(
    text: str,
    *,
    source_url: str | None = None,
    source: Source = Source.MANUAL,
    observed_at: datetime | None = None,
    entity_id: UUID | None = None,
    company_id: UUID | None = None,
) -> tuple[str, list[Event]]:
    """Returns (clean_text, integrity_events). Never rejects the document."""
    ctx = {
        "source": source,
        "source_url": source_url,
        "observed_at": observed_at or utcnow(),
        "entity_id": entity_id,
        "company_id": company_id,
    }

    clean = text
    events: list[Event] = []
    for rule in RULES:
        if rule.pattern is None:
            continue
        clean, found = _apply(rule, clean, ctx)
        events.extend(found)

    clean, found = _strip_stuffing(clean, ctx)
    events.extend(found)
    return _collapse(clean), events


def _apply(rule: Rule, text: str, ctx: dict) -> tuple[str, list[Event]]:
    assert rule.pattern is not None
    pieces: list[str] = []
    events: list[Event] = []
    idx = 0
    for m in rule.pattern.finditer(text):
        pieces.append(text[idx : m.start()])
        pieces.append(rule.join)
        idx = m.end()
        events.append(_event(rule, m.group(0), _extra(rule, m, text), ctx))
    if not events:
        return text, []
    pieces.append(text[idx:])
    return "".join(pieces), events


def _extra(rule: Rule, m: re.Match[str], text: str) -> dict:
    if rule.name == "invisible_unicode":
        lo, hi = max(0, m.start() - 30), min(len(text), m.end() + 30)
        return {
            "codepoints": [f"U+{ord(c):04X}" for c in m.group(0)],
            "context": _visible(text[lo:hi]),
        }
    if rule.name == "encoded_blob":
        return {"decoded_preview": _decode_preview(m.group(0))}
    return {}


def _decode_preview(blob: str) -> str | None:
    """The decoded payload is the demo: it shows what the blob was hiding."""
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
    except (binascii.Error, ValueError):
        return None
    decoded = raw.decode("utf-8", "replace")
    if not decoded:
        return None
    printable = sum(c.isprintable() or c.isspace() for c in decoded)
    return decoded[:300] if printable / len(decoded) >= 0.85 else None


def _strip_stuffing(text: str, ctx: dict) -> tuple[str, list[Event]]:
    rule = RULES_BY_NAME["keyword_stuffing"]
    events: list[Event] = []

    # 1. literal adjacent runs
    pieces: list[str] = []
    idx = 0
    for m in _RUN_RE.finditer(text):
        pieces.append(text[idx : m.start()])
        pieces.append(m.group(1) + " ")
        idx = m.end()
        events.append(_event(rule, m.group(0), {"keyword": m.group(1), "shape": "run"}, ctx))
    pieces.append(text[idx:])
    text = "".join(pieces)

    # 2. frequency: a keyword that dominates the document without repeating adjacently
    tokens = _TOKEN_RE.findall(text)
    if len(tokens) < _STUFF_MIN_TOKENS:
        return text, events
    word, count = Counter(t.lower() for t in tokens).most_common(1)[0]
    share = count / len(tokens)
    if count < _STUFF_MIN_COUNT or share < _STUFF_MIN_SHARE:
        return text, events

    excess = list(re.finditer(rf"\b{re.escape(word)}\b", text, re.IGNORECASE))[_STUFF_KEEP:]
    pieces, idx = [], 0
    for m in excess:
        pieces.append(text[idx : m.start()])
        idx = m.end()
    pieces.append(text[idx:])
    events.append(
        _event(
            rule,
            " … ".join(m.group(0) for m in excess)[:300],
            {"keyword": word, "count": count, "share": round(share, 3), "shape": "frequency"},
            ctx,
        )
    )
    return "".join(pieces), events


def _event(rule: Rule, span: str, extra: dict, ctx: dict) -> Event:
    return Event(
        kind=EventKind.INTEGRITY,
        source=ctx["source"],
        source_url=ctx["source_url"],
        observed_at=ctx["observed_at"],
        entity_id=ctx["entity_id"],
        company_id=ctx["company_id"],
        payload={
            "rule": rule.name,
            "description": rule.description,
            "action": "stripped",
            "offending_text": _visible(span)[:500],
            **extra,
        },
        evidence_span=_visible(span)[:500],  # the exact offending text — this is the trace
        confidence=1.0,
        integrity_flags=[STRIPPED_FLAG],
    )


def _visible(s: str) -> str:
    """An invisible span quotes as nothing at all. Escape it so the trace is readable."""
    return "".join(c if c.isprintable() or c == "\n" else f"\\u{ord(c):04x}" for c in s)


def _collapse(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
