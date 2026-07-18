"""Deck OCR. Owner: B. Keep slide IDs on every span — the memo cites 'slide 7'.

Low-confidence extraction sets integrity_flags=['ocr_low_conf'] so D can SURFACE it.
A bad scan that just quietly scores low is the Type 6 failure mode.

Text-layer PDFs only: a page with no text layer is flagged, never crashed on and
never silently dropped. We do not shell out to an OCR binary.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pdfplumber

from core import llm
from schema.events import Event, EventKind, RawSignal, Source
from sourcing import bus

log = logging.getLogger(__name__)

LOW_CONF_FLAG = "ocr_low_conf"
NO_TEXT_FLAG = "no_text_layer"
HEURISTIC_FLAG = "heuristic_extraction"

MIN_CHARS = 40  # below this a slide is a picture, a title card, or a bad scan
LOW_CONF = 0.35
CLAIM_CONF = 0.8

_SYSTEM = "You extract checkable factual claims from a startup pitch deck."
_PROMPT = """Each slide is marked [slide N]. Return JSON:
{"claims": [{"slide": <int>, "claim": "<one sentence>", "quote": "<exact text from that slide>"}]}

Only assertions an outsider could go and verify: metrics, customers, revenue, growth,
benchmarks, shipped capabilities, dates, partnerships. Skip vision statements, adjectives
and generic marketing. Judge the claim on substance alone — never on who the team is or
where they have been. Every quote must appear verbatim on the slide you attribute it to."""


def extract(pdf_path: Path, company_id: UUID | None = None) -> list[Event]:
    pdf_path = Path(pdf_path)
    events: list[Event] = []
    slides: list[tuple[int, str, float, list[str]]] = []  # (n, clean_text, confidence, flags)

    with pdfplumber.open(pdf_path) as pdf:
        observed_at, date_flags = _deck_date(pdf.metadata, pdf_path)
        for n, page in enumerate(pdf.pages, start=1):
            raw_text = page.extract_text() or ""
            span = f"slide {n}"

            if len(raw_text.strip()) < MIN_CHARS:
                # Flag it. A page we could not read must not look like a page with nothing to say.
                events.append(
                    _integrity(
                        NO_TEXT_FLAG,
                        span,
                        f"{span}: no readable text layer ({len(raw_text.strip())} chars)",
                        pdf_path,
                        observed_at,
                        company_id,
                        date_flags,
                    )
                )
                continue

            prep = bus.prepare(
                RawSignal(
                    source=Source.DECK,
                    source_url=str(pdf_path),
                    content=raw_text,
                    meta={"slide": n, "observed_at": observed_at, "company_id": company_id},
                )
            )
            events.extend(prep.integrity_events)
            conf, flags = _quality(prep.clean_text)
            slides.append((n, prep.clean_text, conf, flags + prep.integrity_flags + date_flags))

    events.extend(_claim_events(slides, pdf_path, observed_at, company_id))
    return events


def _claim_events(
    slides: list[tuple[int, str, float, list[str]]],
    pdf_path: Path,
    observed_at: datetime,
    company_id: UUID | None,
) -> list[Event]:
    if not slides:
        return []
    by_slide = {n: (text, conf, flags) for n, text, conf, flags in slides}
    claims, extra_flags = _llm_claims(slides)
    if claims is None:
        claims, extra_flags = _heuristic_claims(slides), [HEURISTIC_FLAG]

    events = []
    for c in claims:
        n = c.get("slide")
        if n not in by_slide:
            continue
        text, conf, flags = by_slide[n]
        quote = c.get("quote") or ""
        # A quote the slide does not contain is a hallucinated citation — drop the quote,
        # keep the claim, and let the confidence show it.
        verbatim = bool(quote) and quote[:60].lower() in text.lower()
        events.append(
            Event(
                kind=EventKind.DECK_CLAIM,
                source=Source.DECK,
                source_url=str(pdf_path),
                observed_at=observed_at,
                company_id=company_id,
                payload={
                    "claim": c.get("claim", "").strip(),
                    "slide": n,
                    "quote": quote if verbatim else None,
                    "slide_text": text[:1000],
                },
                evidence_span=f"slide {n}" + (f": {quote[:200]}" if verbatim else ""),
                confidence=round(CLAIM_CONF * conf * (1.0 if verbatim else 0.7), 3),
                integrity_flags=sorted(set(flags + extra_flags)),
            )
        )
    return events


def _llm_claims(slides: list[tuple[int, str, float, list[str]]]) -> tuple[list[dict] | None, list]:
    body = "\n\n".join(f"[slide {n}]\n{text}" for n, text, _, _ in slides)
    try:
        # Deck text is founder-supplied: it goes in untrusted=, never into the prompt.
        out = llm.complete(_PROMPT, system=_SYSTEM, tier="fast", untrusted=body, json_mode=True)
    except Exception as exc:  # no key, quota, malformed JSON — degrade, don't crash
        log.warning("deck claim extraction failed, falling back to heuristics: %s", exc)
        return None, []
    claims = out.get("claims") if isinstance(out, dict) else None
    return (claims, []) if isinstance(claims, list) else (None, [])


_SENTENCE_RE = re.compile(r"[^.\n]{25,220}")
_NUMBER_RE = re.compile(r"\d[\d,.]*\s*(?:%|x|k|m|bn?|users?|customers?|ms|gb|tb|qps|rps)?", re.I)


def _heuristic_claims(slides: list[tuple[int, str, float, list[str]]]) -> list[dict]:
    """Offline/no-key fallback: quantified sentences are the checkable ones."""
    claims = []
    for n, text, _, _ in slides:
        for s in _SENTENCE_RE.findall(text):
            s = s.strip()
            if _NUMBER_RE.search(s):
                claims.append({"slide": n, "claim": s, "quote": s})
    return claims[:40]


def _quality(text: str) -> tuple[float, list[str]]:
    """Cheap legibility check. Garbage in a text layer looks like garbage."""
    words = text.split()
    if not words:
        return LOW_CONF, [LOW_CONF_FLAG]
    alpha = sum(c.isalnum() or c.isspace() or c in ".,;:!?%$()/-'\"" for c in text) / len(text)
    long_words = sum(len(w) > 22 for w in words) / len(words)
    if alpha < 0.85 or long_words > 0.15 or len(words) < 8:
        return LOW_CONF, [LOW_CONF_FLAG]
    return 1.0, []


_PDF_DATE_RE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?")


def _deck_date(metadata: dict | None, pdf_path: Path) -> tuple[datetime, list[str]]:
    """CreationDate is the deck's own clock. mtime is a fallback, and it says so."""
    raw = (metadata or {}).get("CreationDate") or (metadata or {}).get("ModDate")
    if isinstance(raw, str) and (m := _PDF_DATE_RE.search(raw)):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        return datetime(y, mo, d, int(m[4] or 0), int(m[5] or 0), tzinfo=timezone.utc), []
    if (parsed := bus.parse_ts(raw)) is not None:
        return parsed, []
    mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime, tz=timezone.utc)
    return mtime, [bus.DATE_INFERRED]


def _integrity(
    rule: str,
    span: str,
    message: str,
    pdf_path: Path,
    observed_at: datetime,
    company_id: UUID | None,
    date_flags: list[str],
) -> Event:
    return Event(
        kind=EventKind.INTEGRITY,
        source=Source.DECK,
        source_url=str(pdf_path),
        observed_at=observed_at,
        company_id=company_id,
        payload={"rule": rule, "description": message, "slide": span},
        evidence_span=span,
        confidence=LOW_CONF,
        integrity_flags=sorted({rule, LOW_CONF_FLAG, *date_flags}),
    )
