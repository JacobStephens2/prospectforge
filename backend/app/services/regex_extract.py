"""Deterministic pre-extraction — emails and phones from raw page text.

Per spec §7.2: run regex BEFORE the LLM. The LLM's job is classification,
association, and evidence quoting — not primary email discovery. Feeding
pre-found emails/phones to the LLM prompt means it associates rather than
guesses, which cuts hallucinations and lets us spend tokens on segmentation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# RFC 5322 is a rabbit hole; this practical pattern is the same one used by
# countless data-extraction libraries and matches >99% of real-world emails.
# `[a-z0-9!#$%&'*+/=?^_`{|}~-]+` allows the documented local-part characters.
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+"
)

# US-centric phone matcher: optional country prefix, then 3-3-4 with any
# common separator. Doesn't try to handle international — pilot sources are
# US schools. False positives on raw 10-digit numbers without separators are
# acceptable because the LLM gets to filter.
_PHONE_RE = re.compile(
    r"(?:\+?1[\s\-.]?)?"          # optional country code
    r"\(?([2-9]\d{2})\)?"         # area code (NANP — can't start with 0 or 1)
    r"[\s\-.]?"
    r"([2-9]\d{2})"               # exchange
    r"[\s\-.]?"
    r"(\d{4})"                    # subscriber
    r"(?!\d)"                     # don't match longer numbers
)

# mailto: links — also valid evidence even if not in plain text body.
_MAILTO_RE = re.compile(r"mailto:([^\s\"'<>?&]+)", re.IGNORECASE)


@dataclass(frozen=True)
class PrePass:
    """Result of deterministic extraction. Lowercased + deduped."""

    emails: tuple[str, ...]
    phones: tuple[str, ...]


def _normalize_phone(area: str, exchange: str, sub: str) -> str:
    """Canonical: (XXX) XXX-XXXX so duplicates collapse in dedupe."""
    return f"({area}) {exchange}-{sub}"


def extract_signals(text: str | None) -> PrePass:
    """Find emails and phones in raw page text. Order-preserving dedupe."""
    if not text:
        return PrePass((), ())

    seen_emails: dict[str, None] = {}
    for m in _EMAIL_RE.finditer(text):
        lower = m.group(0).lower()
        seen_emails.setdefault(lower, None)
    for m in _MAILTO_RE.finditer(text):
        lower = m.group(1).strip().lower()
        # mailto can carry query params; strip them
        lower = lower.split("?", 1)[0]
        if "@" in lower:
            seen_emails.setdefault(lower, None)

    seen_phones: dict[str, None] = {}
    for m in _PHONE_RE.finditer(text):
        seen_phones.setdefault(_normalize_phone(*m.groups()), None)

    return PrePass(tuple(seen_emails.keys()), tuple(seen_phones.keys()))
