"""PDF text extraction (yield workstream step 5).

K-12 source materials are frequently PDFs: board-meeting minutes, faculty
rosters, conference programs, contact directories distributed as
downloadable handouts. Excluding PDFs at discovery time was a load-bearing
yield gap (Perplexity Model Council, all three models converged on this).

pypdf is pure-Python and license-friendly (MIT). When a PDF is an image
scan (no text layer), pypdf returns empty strings — that's the OCR
fallback case, which is deferred to a later iteration because OCR adds
heavy native dependencies (tesseract) and a noisy false-positive rate
that doesn't justify the install footprint yet.

We don't try to preserve layout; the downstream regex pre-pass and LLM
extractor work on flowed text. Encrypted or corrupt PDFs return None.
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

log = logging.getLogger(__name__)


# Generous per-page cap: most board-meeting / faculty PDFs are <50 pages.
# A 500-page PDF would be unusual for our sources and almost certainly
# noise (a curriculum manual, not a contact list). Hard-stop guards
# against runaway memory on adversarial input.
MAX_PDF_PAGES = 300


def extract_pdf_text(body: bytes) -> str | None:
    """Extract text from a PDF byte stream. Returns None when the PDF is
    unreadable (encrypted, corrupt, or zero text-bearing pages); returns
    a non-empty string otherwise.

    Pages are joined with double newlines so the downstream pre-pass sees
    each page as a paragraph break (better for context-bounded regexes
    we may add later, e.g. anchoring at/dot normalization inside a single
    block of text)."""
    if not body:
        return None
    try:
        reader = PdfReader(io.BytesIO(body))
    except (PdfReadError, PdfStreamError, ValueError) as exc:
        log.debug("pdf parse failed: %s", exc)
        return None

    # Encrypted PDFs without an empty password fail at get_page; pypdf
    # raises a generic Exception in some versions, so we attempt a
    # blanket-but-narrow decrypt and bail on persistent failure.
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")  # empty-password rolls of common PDFs
        except Exception:  # noqa: BLE001 - pypdf raises subclasses inconsistently
            return None

    chunks: list[str] = []
    pages = reader.pages[:MAX_PDF_PAGES]
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - pypdf can raise unevent types
            log.debug("pdf page extract failed: %s", exc)
            continue
        text = text.strip()
        if text:
            chunks.append(text)

    if not chunks:
        return None
    return "\n\n".join(chunks)


def is_pdf_content_type(content_type: str | None) -> bool:
    """True if the Content-Type indicates a PDF body (case-insensitive)."""
    if not content_type:
        return False
    return content_type.lower().split(";")[0].strip() == "application/pdf"
