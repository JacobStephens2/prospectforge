"""Email de-obfuscation transforms (yield workstream).

Many K-12 sites hide email addresses from naive scrapers. The most common
patterns we see in pilot sources:

  1. Cloudflare email protection — `<a class="__cf_email__"
     data-cfemail="HEX">[email protected]</a>`. The hex is the email XOR-ed
     against the first byte as a key. Cloudflare's own docs describe this
     and the decoding is deterministic (cited by the Perplexity Model
     Council synthesis; see project memory project_yield_workstream).

  2. HTML entities — `&#64;` for `@`, `&#46;` for `.`, etc. trafilatura
     does NOT always decode these in extracted text, depending on tag
     structure, so the regex pre-pass needs to unescape too.

  3. Bracketed `[at]`/`(at)`/`{at}` and `[dot]`/`(dot)`/`{dot}` (case
     variations and whitespace variations). Common low-effort obfuscation.

Naked " at " and " dot " (without brackets) are intentionally not handled
yet — too many false positives in normal English prose ("email me at the
office, schedule a dot release"). When we have a directory-page detector
(per NCES) we can apply naked at/dot inside detected directory cards only.

All transforms are pure functions over strings. The orchestrator
re-enters them in the crawl worker (HTML-aware) and pre-pass (text-only).
"""

from __future__ import annotations

import html
import re


# Cloudflare email protection: any element carrying data-cfemail="HEX".
# Match the entire enclosing tag so we can replace it with plain text.
# Cloudflare wraps in <a> usually but the pattern shows up on <span> too.
_CF_ANCHOR_RE = re.compile(
    r'<(?P<tag>a|span)\b[^>]*\bdata-cfemail=["\'](?P<hex>[0-9a-fA-F]+)["\'][^>]*>'
    r".*?"
    r"</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)

# Standalone attribute, in case the surrounding tag isn't closed normally
# in the HTML we receive (e.g. minified or partial). Last-resort fallback.
_CF_ATTR_RE = re.compile(
    r'data-cfemail=["\'](?P<hex>[0-9a-fA-F]+)["\']',
    re.IGNORECASE,
)

# Bracket-wrapped @ and . variants. Whitespace inside brackets is tolerated.
# Case-insensitive on `at` / `dot`.
_BRACKET_AT_RE = re.compile(
    r"\s*[\[\(\{]\s*(?:at|@)\s*[\]\)\}]\s*",
    re.IGNORECASE,
)
_BRACKET_DOT_RE = re.compile(
    r"\s*[\[\(\{]\s*(?:dot|\.)\s*[\]\)\}]\s*",
    re.IGNORECASE,
)


def _decode_cf_email_hex(hex_str: str) -> str | None:
    """Decode a Cloudflare data-cfemail hex blob. Returns None on malformed
    input (odd length, non-hex chars, or decoded chars outside printable
    ASCII)."""
    if len(hex_str) < 4 or len(hex_str) % 2 != 0:
        return None
    try:
        key = int(hex_str[:2], 16)
        out_chars: list[str] = []
        for i in range(2, len(hex_str), 2):
            ch = int(hex_str[i : i + 2], 16) ^ key
            if ch < 0x20 or ch > 0x7E:
                # Decoded a non-printable byte — almost certainly not a real
                # email and we don't want to inject garbage into the page text.
                return None
            out_chars.append(chr(ch))
    except ValueError:
        return None
    decoded = "".join(out_chars)
    return decoded if "@" in decoded else None


def decode_cloudflare_emails(html_text: str) -> str:
    """Replace any <a|span data-cfemail=...>...</a|span> wrappers with the
    decoded email address as plain text, so trafilatura's extraction (and
    the downstream regex pre-pass) can see it. Idempotent: a second call
    is a no-op because the cfemail attributes are gone."""
    if not html_text or "cfemail" not in html_text.lower():
        return html_text

    def _sub_anchor(match: re.Match[str]) -> str:
        decoded = _decode_cf_email_hex(match.group("hex"))
        return decoded if decoded else match.group(0)

    out = _CF_ANCHOR_RE.sub(_sub_anchor, html_text)

    # Mop up any orphan data-cfemail attributes the anchor regex missed
    # (malformed HTML, nested structures). Replace just the attribute with
    # a space-prefixed plain email so the surrounding tag stays valid.
    def _sub_attr(match: re.Match[str]) -> str:
        decoded = _decode_cf_email_hex(match.group("hex"))
        return f' data-cfemail-decoded="{decoded}"' if decoded else match.group(0)

    out = _CF_ATTR_RE.sub(_sub_attr, out)
    return out


def decode_html_entities(text: str) -> str:
    """html.unescape handles all standard HTML entities, named and numeric.
    Idempotent — no entities means no change."""
    if not text or "&" not in text:
        return text
    return html.unescape(text)


def normalize_bracketed_at_dot(text: str) -> str:
    """Replace [at]/(at)/{at} variants with @ and [dot]/(dot)/{dot} with .
    so the email regex can match an otherwise-obfuscated address. Surrounding
    whitespace is folded out so 'jane [ at ] school [ dot ] edu' →
    'jane@school.edu' rather than 'jane @ school . edu' (which the email
    regex would still miss)."""
    if not text:
        return text
    text = _BRACKET_AT_RE.sub("@", text)
    text = _BRACKET_DOT_RE.sub(".", text)
    return text


def deobfuscate_html(html_text: str) -> str:
    """HTML-aware path: decode Cloudflare cfemail spans. Run BEFORE
    trafilatura strips tags, otherwise the data-cfemail attribute is lost.
    The text-level transforms run later in the pre-pass on the extracted
    markdown."""
    return decode_cloudflare_emails(html_text)


def deobfuscate_text(text: str) -> str:
    """Text-only path: HTML entities + bracketed at/dot normalization. Safe
    to run on already-extracted markdown — both transforms are idempotent
    and leave non-obfuscated text alone."""
    text = decode_html_entities(text)
    text = normalize_bracketed_at_dot(text)
    return text
