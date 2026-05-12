"""Evidence verifier tests — the gate that decides whether a contact is
auto-exportable or has to be human-reviewed (spec §5.3)."""

from __future__ import annotations

from app.services.evidence import verify_excerpt


SOURCE = """
Phoenix Independent School District — 2024-25 Faculty Directory.
Band Director: Jane Smith. Email: jane.smith@phoenixisd.org. Office: (602) 555-0100.
Theater Director: Carlos Rivera, MFA. Reach him at crivera@phoenixisd.org.
The high school marching band performs at the State Fair every fall.
""".strip()


def test_verified_exact_match() -> None:
    r = verify_excerpt("Band Director: Jane Smith", SOURCE)
    assert r.status == "verified"
    assert r.score == 1.0


def test_verified_case_insensitive() -> None:
    r = verify_excerpt("BAND DIRECTOR: JANE SMITH", SOURCE)
    assert r.status == "verified"


def test_verified_whitespace_normalized() -> None:
    """Re-flowed line breaks shouldn't break the match."""
    r = verify_excerpt("Band Director:\n\n   Jane\tSmith", SOURCE)
    assert r.status == "verified"


def test_fuzzy_substring_within_threshold() -> None:
    """LLM rephrases slightly — verifier should still accept ≥0.9 similarity."""
    # Drop one word from a long excerpt → coverage stays high.
    r = verify_excerpt("Band Director Jane Smith. Email: jane.smith@phoenixisd.org", SOURCE)
    assert r.status in ("verified", "fuzzy")


def test_fabricated_excerpt_rejected() -> None:
    """LLM invents content the page doesn't contain."""
    r = verify_excerpt(
        "Principal Sarah Johnson runs the international travel program",
        SOURCE,
    )
    assert r.status == "failed"


def test_empty_excerpt_fails() -> None:
    assert verify_excerpt("", SOURCE).status == "failed"
    assert verify_excerpt(None, SOURCE).status == "failed"
    assert verify_excerpt("   ", SOURCE).status == "failed"


def test_empty_source_fails() -> None:
    assert verify_excerpt("anything", "").status == "failed"
    assert verify_excerpt("anything", None).status == "failed"


def test_short_partial_overlap_still_fails() -> None:
    """A 5-word excerpt where only 2 words appear shouldn't score 0.9+."""
    r = verify_excerpt("Robotics champions at MIT exchange", SOURCE)
    assert r.status == "failed"
