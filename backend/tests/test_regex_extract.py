"""Deterministic email/phone pre-extraction tests."""

from __future__ import annotations

from app.services.regex_extract import extract_signals


def test_extract_plain_email() -> None:
    r = extract_signals("Contact Jane at jane.smith@school.k12.tx.us for details.")
    assert r.emails == ("jane.smith@school.k12.tx.us",)


def test_extract_mailto_link() -> None:
    """mailto: hrefs are valid evidence even when the address isn't visible text."""
    html = '<a href="mailto:Director@SCHOOL.org?subject=Tours">contact us</a>'
    r = extract_signals(html)
    assert r.emails == ("director@school.org",)


def test_extract_dedupe_case_insensitive() -> None:
    text = "Email: ALICE@northwind.example or alice@northwind.example for info."
    r = extract_signals(text)
    assert r.emails == ("alice@northwind.example",)


def test_phone_extraction_variants() -> None:
    text = """
    Office: (512) 555-1234
    Direct: 512-555-9876
    Cell:   +1 512.555.1212
    Hot:    5125550000
    """
    r = extract_signals(text)
    # All normalize to (XXX) XXX-XXXX
    assert "(512) 555-1234" in r.phones
    assert "(512) 555-9876" in r.phones
    assert "(512) 555-1212" in r.phones
    assert "(512) 555-0000" in r.phones


def test_phone_nanp_rules() -> None:
    """North American Numbering Plan: area code can't start with 0 or 1."""
    r = extract_signals("Bogus: 0-12-555-1234, also 1-12-555-1234")
    # Those exact strings are unmatchable (area code starts with 0 or 1).
    assert r.phones == ()


def test_no_signals_in_empty_or_none() -> None:
    assert extract_signals(None).emails == ()
    assert extract_signals("").phones == ()
    assert extract_signals("\n\n\t").emails == ()


def test_order_preserving_dedupe() -> None:
    """First occurrence wins so the LLM sees emails in source order."""
    text = "Lead: alice@a.com, then bob@b.com, then alice@a.com again."
    r = extract_signals(text)
    assert r.emails == ("alice@a.com", "bob@b.com")


def test_email_not_treated_as_phone() -> None:
    """Numbers inside an email shouldn't show up as phones."""
    r = extract_signals("Reach me at user1234567890@example.com.")
    assert r.emails == ("user1234567890@example.com",)
    assert r.phones == ()
