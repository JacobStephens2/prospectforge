"""OCR-based extraction for image-obfuscated contact info.

Some K-12 sites publish staff emails as PNGs/JPGs to defeat scrapers — the
visible text says "jane@school.edu" but it's a raster image. The crawler
text extraction sees nothing; the email is genuinely there for any human
reader. This module fills that gap by OCR'ing likely-contact images and
appending the recognized text into the page's extracted markdown so the
regex pre-pass and LLM see it.

Heuristics (cheap):
  1. We only OCR images whose alt-text, filename, or class/id mentions
     "email", "contact", "staff", or a person-y hint. Blanket-OCR'ing every
     image on every page is too expensive and yields too many false hits.
  2. We cap at OCR_MAX_IMAGES_PER_PAGE per fetched HTML page.
  3. Tesseract/pytesseract are OPTIONAL at runtime. If either is missing,
     this module degrades to a no-op so the system still works without OCR.

System dep: tesseract-ocr binary (apt: tesseract-ocr). Python deps:
pytesseract + Pillow (in requirements.txt). When tesseract isn't installed
in the container, find_candidate_image_urls() still runs (pure regex), but
ocr_image_bytes() returns "" — operators see "0 images OCRed" instead of a
crash.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

OCR_MAX_IMAGES_PER_PAGE = 3

# Regex over <img> tags. We pick out src, alt, class, id together so the
# heuristic can decide whether the image is contact-flavored without a full
# HTML parser.
_IMG_TAG_RE = re.compile(r"<img\b([^>]*?)/?>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r'(?P<name>\w[\w-]*)\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|(?P<bare>[^\s>]+))',
    re.IGNORECASE,
)

_CONTACT_HINT_RE = re.compile(
    r"(email|e-mail|mail|contact|staff|faculty|director|teacher|head|principal)",
    re.IGNORECASE,
)
# Tracking pixels, sprites, icon sheets — never carry useful text.
_BLOCKED_FILENAME_RE = re.compile(
    r"(sprite|icon|logo|favicon|pixel|tracking|spacer|placeholder|loader)",
    re.IGNORECASE,
)


def _parse_attrs(attrs_blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(attrs_blob):
        val = m.group("dq") or m.group("sq") or m.group("bare") or ""
        out[m.group("name").lower()] = val
    return out


def _looks_contactish(attrs: dict[str, str]) -> bool:
    """True if any of alt/title/class/id/src hints at a contact image."""
    blob_parts = [
        attrs.get("alt", ""),
        attrs.get("title", ""),
        attrs.get("class", ""),
        attrs.get("id", ""),
        attrs.get("src", ""),
    ]
    blob = " ".join(blob_parts)
    if not blob.strip():
        return False
    if _BLOCKED_FILENAME_RE.search(attrs.get("src", "")):
        return False
    return bool(_CONTACT_HINT_RE.search(blob))


def find_contact_image_candidates(
    html: str | None, base_url: str | None = None
) -> list[str]:
    """Return absolute URLs of <img> tags that look like contact images.

    Order-preserving and deduped. Capped at OCR_MAX_IMAGES_PER_PAGE so a
    page with 200 staff photos doesn't trigger 200 OCR calls — operators
    can re-run with a higher cap if a particular org needs it.
    """
    if not html or "<img" not in html.lower():
        return []
    out: dict[str, None] = {}
    for m in _IMG_TAG_RE.finditer(html):
        attrs = _parse_attrs(m.group(1))
        src = attrs.get("src", "").strip()
        if not src or src.startswith("data:"):
            # data: URIs we'd need to base64-decode separately — out of
            # scope for the first cut.
            continue
        if not _looks_contactish(attrs):
            continue
        absolute = urljoin(base_url, src) if base_url else src
        out.setdefault(absolute, None)
        if len(out) >= OCR_MAX_IMAGES_PER_PAGE:
            break
    return list(out.keys())


def ocr_image_bytes(image_bytes: bytes) -> str:
    """OCR a raw image. Returns the recognized text, or "" if OCR is not
    available (missing pytesseract, missing tesseract binary, unreadable
    image, etc). This MUST never raise — the crawl path can't afford to
    fail an entire page because the OCR library isn't installed."""
    if not image_bytes:
        return ""
    try:
        import io

        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        # Logged once per process to avoid spamming every fetched page.
        _warn_once_ocr_unavailable()
        return ""

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            text = pytesseract.image_to_string(img)
    except Exception as exc:
        logger.debug("OCR failed on image: %s", exc)
        return ""

    return text.strip()


_warned_ocr_unavailable = False


def _warn_once_ocr_unavailable() -> None:
    global _warned_ocr_unavailable
    if _warned_ocr_unavailable:
        return
    _warned_ocr_unavailable = True
    logger.warning(
        "image_ocr: pytesseract/Pillow not installed — image-obfuscated "
        "contact info will not be recovered. Install tesseract-ocr (apt) "
        "and pip install pytesseract Pillow to enable."
    )


def ocr_is_available() -> bool:
    """Cheap probe: do we have the runtime deps to OCR at all? Used by
    callers that want to skip the image-fetch step entirely when OCR
    won't run anyway."""
    try:
        import pytesseract  # type: ignore[import-not-found,unused-ignore]  # noqa: F401
        from PIL import Image  # type: ignore[import-not-found,unused-ignore]  # noqa: F401
    except ImportError:
        return False
    return True
