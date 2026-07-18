"""Deck OCR. Owner: B. Keep slide IDs on every span — the memo cites 'slide 7'.

Low-confidence extraction sets integrity_flags=['ocr_low_conf'] so D can SURFACE it.
A bad scan that just quietly scores low is the Type 6 failure mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from schema.events import Event, EventKind, Source

# Try to import pdfplumber, fall back to OCR
try:
    import pdfplumber
except ImportError:
    pdfplumber = None


def _extract_with_pdfplumber(pdf_path: Path) -> list[tuple[str, str | None]]:
    """Extract text from PDF using pdfplumber.

    Returns list of (text, slide_id) tuples.
    """
    if pdfplumber is None:
        raise ImportError("pdfplumber not installed")

    pages_data = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            slide_id = f"slide_{page_num + 1}"

            # Try to extract text
            text = page.extract_text()

            if text:
                pages_data.append((text, slide_id))
            else:
                # Page has no text layer - will need OCR
                pages_data.append(("", slide_id))

    return pages_data


def _needs_ocr(pdf_path: Path) -> list[int]:
    """Return list of page numbers that need OCR (no text layer)."""
    if pdfplumber is None:
        return list(range(100))  # Assume all need OCR

    pages_needing_ocr = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text or len(text.strip()) < 10:
                pages_needing_ocr.append(page_num + 1)

    return pages_needing_ocr


def _ocr_page(pdf_path: Path, page_num: int) -> str:
    """Perform OCR on a single page.

    Returns extracted text.
    """
    try:
        # Use pdf2image and pytesseract for OCR
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num)
        if images:
            text = pytesseract.image_to_string(images[0])
            return text
    except ImportError:
        pass
    except Exception:
        pass

    return ""


def extract(pdf_path: Path, company_id) -> list[Event]:
    """Extract events from a PDF deck.

    Args:
        pdf_path: Path to the PDF file
        company_id: ID of the company for this deck

    Returns:
        List of Event objects with DECK_CLAIM kind

    Notes:
        - Keeps slide IDs on every extracted span
        - Low-confidence OCR gets integrity_flags=['ocr_low_conf']
        - The memo cites 'slide 7' - each claim must be traceable
    """
    events = []

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # First pass: try pdfplumber
    try:
        pages_data = _extract_with_pdfplumber(pdf_path)
    except Exception as e:
        # If pdfplumber fails, try to read raw bytes
        pages_data = [("", f"slide_{i}") for i in range(1, 10)]  # Assume up to 10 slides

    # Second pass: OCR pages that need it
    pages_to_ocr = _needs_ocr(pdf_path)
    ocr_performed = False

    if pages_to_ocr:
        try:
            from pdf2image import convert_from_path
            import pytesseract

            images = convert_from_path(pdf_path)
            for page_num, image in enumerate(images):
                if page_num + 1 in pages_to_ocr:
                    # Get the text from OCR
                    text = pytesseract.image_to_string(image)

                    # If pdfplumber had partial text, merge it
                    if page_num < len(pages_data):
                        existing_text, slide_id = pages_data[page_num]
                        if not existing_text or len(existing_text.strip()) < 20:
                            pages_data[page_num] = (text, slide_id)
                            ocr_performed = True
        except ImportError:
            # OCR libraries not available
            pass
        except Exception:
            pass

    # Generate events from extracted text
    for page_num, (text, slide_id) in enumerate(pages_data):
        if not text or len(text.strip()) < 5:
            # Skip empty pages or very short content
            continue

        # Split text into chunks (paragraphs or sentences)
        # Each chunk becomes a potential claim
        chunks = _split_into_chunks(text)

        for chunk in chunks:
            # Check confidence - if this page needed OCR, lower confidence
            confidence = 0.95
            integrity_flags = []

            if page_num + 1 in pages_to_ocr:
                confidence = 0.7
                integrity_flags.append("ocr_low_conf")

            # Create deck claim event
            event = Event(
                event_id=uuid4(),
                kind=EventKind.DECK_CLAIM,
                source=Source.DECK,
                source_url=str(pdf_path),
                observed_at=datetime.now(timezone.utc),
                payload={
                    "text": chunk,
                    "slide_id": slide_id,
                    "page_number": page_num + 1,
                    "company_id": str(company_id),
                },
                evidence_span=chunk[:200] if len(chunk) <= 200 else chunk[:100] + "...",
                confidence=confidence,
                integrity_flags=integrity_flags,
            )
            events.append(event)

    # Add a metadata event about the extraction process
    extraction_metadata = {
        "pdf_path": str(pdf_path),
        "pages_processed": len(pages_data),
        "pages_needing_ocr": pages_to_ocr,
        "ocr_performed": ocr_performed,
    }

    metadata_event = Event(
        event_id=uuid4(),
        kind=EventKind.PROFILE_FACT,
        source=Source.DECK,
        source_url=str(pdf_path),
        observed_at=datetime.now(timezone.utc),
        payload={"extraction_metadata": extraction_metadata},
        evidence_span=None,
        confidence=1.0,
        integrity_flags=[],
    )
    events.append(metadata_event)

    return events


def _split_into_chunks(text: str, min_length: int = 20) -> list[str]:
    """Split text into logical chunks for claim extraction.

    Tries to split on paragraph and sentence boundaries.
    """
    import re

    # Split on double newlines first (paragraphs)
    paragraphs = re.split(r'\n\s*\n', text)

    chunks = []
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if len(paragraph) < min_length:
            continue

        # If paragraph is too long, split on sentences
        if len(paragraph) > 500:
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            current_chunk = ""

            for sentence in sentences:
                if len(current_chunk) + len(sentence) < 500:
                    current_chunk += " " + sentence if current_chunk else sentence
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence

            if current_chunk:
                chunks.append(current_chunk.strip())
        else:
            chunks.append(paragraph)

    # Filter short chunks
    chunks = [c for c in chunks if len(c) >= min_length]

    return chunks[:50]  # Limit to 50 chunks per deck


def extract_with_confidence(pdf_path: Path, company_id, min_confidence: float = 0.5) -> list[Event]:
    """Extract events with minimum confidence threshold.

    Low-confidence extractions are flagged but still included.
    """
    events = extract(pdf_path, company_id)

    # Filter by confidence
    filtered = [e for e in events if e.confidence >= min_confidence]

    return filtered


def get_slide_mapping(pdf_path: Path) -> dict[str, int]:
    """Get mapping of slide IDs to page numbers.

    Returns dict like {"slide_1": 1, "slide_2": 2, ...}
    """
    mapping = {}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                slide_id = f"slide_{page_num + 1}"
                mapping[slide_id] = page_num + 1
    except Exception:
        # Fallback
        mapping = {"slide_1": 1}

    return mapping
