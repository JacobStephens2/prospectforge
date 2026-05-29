"""Email-pattern inference."""

from __future__ import annotations

import pytest

from app.services.email_pattern import (
    InferredPattern,
    generate_email,
    infer_pattern,
    split_name,
)


# ---------- split_name ----------


@pytest.mark.parametrize(
    "name,first,last",
    [
        ("Jane Smith", "jane", "smith"),
        ("Jane Q. Smith", "jane", "smith"),
        ("Dr. Jane Smith", "jane", "smith"),
        ("Jane Smith Jr.", "jane", "smith"),
        ("Anna Kowal-Byrne", "anna", "kowal-byrne"),
        ("  Jane   Smith  ", "jane", "smith"),
    ],
)
def test_split_name(name, first, last):
    p = split_name(name)
    assert p is not None
    assert p.first == first
    assert p.last == last


def test_split_name_mononym_returns_none():
    assert split_name("Cher") is None
    assert split_name("") is None
    assert split_name("Dr.") is None


# ---------- infer_pattern ----------


def test_infer_first_last():
    samples = [
        ("John Doe", "john.doe@school.org"),
        ("Jane Smith", "jane.smith@school.org"),
    ]
    out = infer_pattern(samples)
    assert out is not None
    assert out.pattern == "first.last"
    assert out.domain == "school.org"
    assert out.sample_size == 2


def test_infer_flast():
    samples = [
        ("John Doe", "jdoe@school.org"),
        ("Jane Smith", "jsmith@school.org"),
        ("Bob Jones", "bjones@school.org"),
    ]
    out = infer_pattern(samples)
    assert out is not None
    assert out.pattern == "flast"
    assert out.sample_size == 3


def test_infer_requires_min_samples():
    # Only one pair → below the 2-sample floor.
    samples = [("John Doe", "john.doe@school.org")]
    assert infer_pattern(samples) is None


def test_infer_ignores_role_accounts():
    """Role accounts (admissions@, info@) don't match any name pattern and
    are silently ignored — they shouldn't block a clean inference."""
    samples = [
        ("John Doe", "john.doe@school.org"),
        ("Admissions Office", "admissions@school.org"),
        ("Jane Smith", "jane.smith@school.org"),
    ]
    out = infer_pattern(samples)
    assert out is not None
    assert out.pattern == "first.last"


def test_infer_skips_off_domain_pairs():
    samples = [
        ("John Doe", "john.doe@school.org"),
        ("Jane Smith", "jane.smith@school.org"),
        ("Bob Jones", "bob.jones@other.com"),  # different domain, ignored
    ]
    out = infer_pattern(samples)
    assert out is not None
    assert out.domain == "school.org"
    assert out.sample_size == 2


def test_infer_no_agreement_returns_none():
    """Two people, two different patterns → no 2-vote winner."""
    samples = [
        ("John Doe", "jdoe@school.org"),      # flast
        ("Jane Smith", "jane.smith@school.org"),  # first.last
    ]
    assert infer_pattern(samples) is None


def test_infer_strips_plus_tag():
    samples = [
        ("John Doe", "john.doe+newsletter@school.org"),
        ("Jane Smith", "jane.smith@school.org"),
    ]
    out = infer_pattern(samples)
    assert out is not None
    assert out.pattern == "first.last"


# ---------- generate_email ----------


def test_generate_email_first_last():
    pat = InferredPattern(
        pattern="first.last", domain="school.org", sample_size=3, confidence=1.0
    )
    assert generate_email("Anna Kowal-Byrne", pat) == "anna.kowal-byrne@school.org"


def test_generate_email_flast():
    pat = InferredPattern(
        pattern="flast", domain="school.org", sample_size=3, confidence=1.0
    )
    assert generate_email("Anna Kowal-Byrne", pat) == "akowal-byrne@school.org"


def test_generate_email_mononym_returns_none():
    pat = InferredPattern(
        pattern="first.last", domain="school.org", sample_size=3, confidence=1.0
    )
    assert generate_email("Cher", pat) is None


def test_round_trip_inference():
    """Infer from a domain's samples, then generate for a new name."""
    samples = [
        ("John Doe", "jdoe@hillcrestacademy.example"),
        ("Jane Smith", "jsmith@hillcrestacademy.example"),
        ("Michelle Halvorsen", "mhalvorsen@hillcrestacademy.example"),
    ]
    pat = infer_pattern(samples)
    assert pat is not None
    assert pat.pattern == "flast"
    candidate = generate_email("Anna Kowal-Byrne", pat)
    assert candidate == "akowal-byrne@hillcrestacademy.example"
