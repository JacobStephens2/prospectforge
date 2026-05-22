"""Email de-obfuscation transforms — purely string-in / string-out."""

from __future__ import annotations

import pytest

from app.services.deobfuscate import (
    _decode_cf_email_hex,
    decode_cloudflare_emails,
    decode_html_entities,
    deobfuscate_html,
    deobfuscate_text,
    normalize_bracketed_at_dot,
)
from app.services.regex_extract import extract_signals


# ---------- Cloudflare cfemail ----------


def _encode_cf_email(email: str, key: int = 0x42) -> str:
    """Inverse of _decode_cf_email_hex for test fixture generation."""
    out = f"{key:02x}"
    for ch in email:
        out += f"{(ord(ch) ^ key):02x}"
    return out


def test_decode_cf_email_hex_round_trip():
    encoded = _encode_cf_email("jane@school.edu")
    assert _decode_cf_email_hex(encoded) == "jane@school.edu"


def test_decode_cf_email_hex_rejects_garbage():
    # Odd length, non-hex, decoded non-printable — all None.
    assert _decode_cf_email_hex("abc") is None
    assert _decode_cf_email_hex("xyz123") is None
    # Decoded "AB" with key 0x01 = chr(0x40) chr(0x43) = "@C" — no '@' wrap
    # rule means we also reject anything without '@', but a 2-char decoded
    # string starting with @ would still be too short to be an email.
    # Hand-craft a hex that decodes to non-printable: key 0xFF, byte 0x01
    # = chr(0xFE) which is > 0x7E.
    assert _decode_cf_email_hex("ff01") is None


def test_decode_cf_email_hex_rejects_decoded_without_at():
    # "hello" XOR 0x42 decodes to chars but contains no @.
    encoded = _encode_cf_email("hello")
    assert _decode_cf_email_hex(encoded) is None


def test_decode_cloudflare_emails_replaces_anchor_with_plain_text():
    encoded = _encode_cf_email("director@northwind.example")
    html = (
        f'<p>Contact <a href="/cdn-cgi/l/email-protection" '
        f'class="__cf_email__" data-cfemail="{encoded}">[email&#160;protected]</a> '
        f"for tours.</p>"
    )
    out = decode_cloudflare_emails(html)
    assert "director@northwind.example" in out
    assert "data-cfemail" not in out  # the anchor was fully replaced


def test_decode_cloudflare_emails_handles_span_wrapper():
    encoded = _encode_cf_email("coach@school.k12.tx.us")
    html = f'<span class="__cf_email__" data-cfemail="{encoded}">[email]</span>'
    assert "coach@school.k12.tx.us" in decode_cloudflare_emails(html)


def test_decode_cloudflare_emails_handles_multiple():
    e1 = _encode_cf_email("a@x.org")
    e2 = _encode_cf_email("b@y.org")
    html = (
        f'<a data-cfemail="{e1}">[p]</a> and '
        f'<a data-cfemail="{e2}">[p]</a>'
    )
    out = decode_cloudflare_emails(html)
    assert "a@x.org" in out and "b@y.org" in out


def test_decode_cloudflare_emails_no_op_without_cfemail():
    html = "<p>plain page with no obfuscation</p>"
    assert decode_cloudflare_emails(html) == html


def test_decode_cloudflare_emails_attribute_fallback():
    """If the anchor regex doesn't match (e.g. tag has unusual structure)
    we still decode the bare attribute as a defensive last pass."""
    encoded = _encode_cf_email("z@w.edu")
    # Self-closing-ish, no inner content / closer in normal place
    html = f'<img data-cfemail="{encoded}" />'
    out = decode_cloudflare_emails(html)
    assert "z@w.edu" in out


def test_decode_cloudflare_emails_idempotent():
    encoded = _encode_cf_email("dean@example.edu")
    html = f'<a data-cfemail="{encoded}">[p]</a>'
    once = decode_cloudflare_emails(html)
    twice = decode_cloudflare_emails(once)
    assert once == twice


# ---------- HTML entities ----------


def test_decode_html_entities_at_and_dot():
    assert decode_html_entities("jane&#64;school&#46;edu") == "jane@school.edu"
    assert decode_html_entities("jane&commat;school&period;edu") == "jane@school.edu"


def test_decode_html_entities_passes_through_plain_text():
    s = "no entities here"
    assert decode_html_entities(s) == s


def test_decode_html_entities_empty():
    assert decode_html_entities("") == ""


# ---------- bracketed at / dot ----------


@pytest.mark.parametrize(
    "obfuscated,expected",
    [
        ("jane [at] school [dot] edu", "jane@school.edu"),
        ("jane(at)school(dot)edu", "jane@school.edu"),
        ("jane {at} school {dot} edu", "jane@school.edu"),
        ("jane [AT] school [DOT] edu", "jane@school.edu"),
        ("jane [@] school [.] edu", "jane@school.edu"),
        ("jane[at]school[dot]k12[dot]tx[dot]us", "jane@school.k12.tx.us"),
        # Whitespace inside brackets too.
        ("jane [ at ] school [ dot ] edu", "jane@school.edu"),
    ],
)
def test_normalize_bracketed_at_dot(obfuscated: str, expected: str):
    assert normalize_bracketed_at_dot(obfuscated) == expected


def test_normalize_bracketed_at_dot_leaves_naked_words_alone():
    # We intentionally don't touch naked ' at '/' dot ' — too many false
    # positives in normal English prose.
    s = "Email me at the school dot release party."
    assert normalize_bracketed_at_dot(s) == s


def test_normalize_bracketed_at_dot_empty():
    assert normalize_bracketed_at_dot("") == ""


# ---------- orchestrators ----------


def test_deobfuscate_html_decodes_cfemail_only():
    """deobfuscate_html runs before trafilatura, so it only touches
    HTML-shaped patterns; text-level transforms wait for the pre-pass."""
    encoded = _encode_cf_email("dean@example.edu")
    html = (
        f'<p>Email <a data-cfemail="{encoded}">[p]</a> or '
        f'<a href="mailto:other&#64;example.edu">other</a></p>'
    )
    out = deobfuscate_html(html)
    assert "dean@example.edu" in out
    # &#64; in the mailto NOT decoded here — that happens in deobfuscate_text.
    assert "&#64;" in out


def test_deobfuscate_text_decodes_entities_and_brackets():
    s = "Reach Jane at jane&#64;school&#46;edu or bob [at] school [dot] edu"
    out = deobfuscate_text(s)
    assert "jane@school.edu" in out
    assert "bob@school.edu" in out


# ---------- end-to-end: extract_signals sees the decoded email ----------


def test_extract_signals_finds_cfemail_after_html_decode():
    """The realistic flow: raw HTML → deobfuscate_html → trafilatura
    (skipped in this unit test by passing markdown-like text) → pre-pass."""
    encoded = _encode_cf_email("director@northwind.example")
    raw_html = (
        f'<p>Contact <a class="__cf_email__" data-cfemail="{encoded}">'
        f"[email protected]</a></p>"
    )
    decoded_text = deobfuscate_html(raw_html)
    result = extract_signals(decoded_text)
    assert "director@northwind.example" in result.emails


def test_extract_signals_finds_entity_encoded_email():
    text = "Contact: jane&#64;school&#46;edu"
    result = extract_signals(text)
    assert "jane@school.edu" in result.emails


def test_extract_signals_finds_bracketed_email():
    text = "Reach the band director at smith [at] highschool [dot] org for trip info."
    result = extract_signals(text)
    assert "smith@highschool.org" in result.emails


def test_extract_signals_plain_email_unaffected_by_deobfuscation():
    """Pre-existing tests must still pass — deobfuscation is additive."""
    text = "Contact Jane at jane.smith@school.k12.tx.us for details."
    result = extract_signals(text)
    assert result.emails == ("jane.smith@school.k12.tx.us",)


def test_extract_markdown_decodes_cfemail_through_trafilatura():
    """Regression guard for the crawl-worker integration: raw HTML with a
    Cloudflare cfemail anchor must reach the markdown with the email
    visible. Trafilatura otherwise strips the data-cfemail attribute."""
    from app.workers.crawl import _extract_markdown

    encoded = _encode_cf_email("director@northwind.example")
    html = (
        "<html><body>"
        "<h1>Contact</h1>"
        "<p>For tour bookings please email "
        f'<a class="__cf_email__" data-cfemail="{encoded}">[email protected]</a>'
        " or call ahead. We staff a coordinator weekdays.</p>"
        "</body></html>"
    ).encode("utf-8")
    markdown = _extract_markdown(html, "text/html")
    assert markdown is not None, "trafilatura returned None on valid HTML"
    assert "director@northwind.example" in markdown
