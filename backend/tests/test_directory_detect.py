"""Staff-directory page detection — string-similarity scoring."""

from __future__ import annotations

import pytest

from app.services.directory_detect import (
    DEFAULT_SIMILARITY_THRESHOLD,
    score_directory_likelihood,
)


@pytest.mark.parametrize(
    "title",
    [
        "Staff Directory",
        "Faculty Directory",
        "Faculty & Staff",
        "Faculty and Staff",
        "Our Staff",
        "Teachers",
        "School Staff",
        "Performing Arts Staff",
    ],
)
def test_title_matches_curated_phrases(title: str):
    result = score_directory_likelihood(title=title)
    assert result.is_directory, f"{title!r} should match (got {result.score:.2f})"
    assert result.matched_field == "title"


def test_non_directory_title_does_not_match():
    result = score_directory_likelihood(title="Latest News and Events")
    assert not result.is_directory


def test_anchor_text_matches():
    result = score_directory_likelihood(anchor_text="View our Faculty Directory")
    # Anchor contains the phrase as a substring → high score.
    assert result.is_directory
    assert result.matched_field == "anchor"


def test_body_prefix_matches_when_phrase_appears_at_top():
    body = (
        "Faculty and Staff\n\n"
        "Welcome to our school's staff page. Below is a list of all\n"
        "teachers and administrators currently on our roster.\n"
    )
    result = score_directory_likelihood(body_prefix=body)
    assert result.is_directory
    assert result.matched_field == "body"


def test_body_prefix_caps_sample_size():
    """Mention of 'faculty directory' deep in the page should not match
    when only the top is sampled."""
    body = "Welcome.\n\n" + ("x" * 600) + "\n\nfaculty directory link"
    # Sample only first 100 chars → phrase out of window → no match.
    result = score_directory_likelihood(body_prefix=body, body_prefix_chars=100)
    assert not result.is_directory


def test_best_signal_across_inputs_wins():
    """If anchor is weak but title is strong, title wins."""
    r = score_directory_likelihood(
        title="Faculty Directory",
        anchor_text="click here",
        body_prefix="this is some unrelated body text",
    )
    assert r.matched_field == "title"
    assert r.is_directory


def test_empty_inputs_score_zero():
    r = score_directory_likelihood()
    assert r.score == 0.0
    assert not r.is_directory
    r2 = score_directory_likelihood(title=None, anchor_text="", body_prefix=None)
    assert r2.score == 0.0


def test_threshold_constant_matches_nces():
    """Smoke test: the threshold tracks the NCES paper's recommendation."""
    assert 0.85 <= DEFAULT_SIMILARITY_THRESHOLD <= 0.95
