"""URL scoring — staff/contact paths boost, news/blog deprio."""

from __future__ import annotations

import pytest

from app.services.url_scoring import rank_urls, score_url


@pytest.mark.parametrize(
    "url",
    [
        "https://lincoln-high.org/staff",
        "https://lincoln-high.org/staff/",
        "https://lincoln-high.org/staff/directory",
        "https://lincoln-high.org/about/faculty/",
        "https://lincoln-high.org/contact/",
        "https://lincoln-high.org/our-team",
        "https://lincoln-high.org/leadership",
        "https://lincoln-high.org/academics/department/band",
        "https://lincoln-high.org/performing-arts/staff",
    ],
)
def test_boost_paths_score_positive(url: str):
    assert score_url(url) > 0


@pytest.mark.parametrize(
    "url",
    [
        "https://lincoln-high.org/news",
        "https://lincoln-high.org/blog/2024/01/welcome",
        "https://lincoln-high.org/calendar",
        "https://lincoln-high.org/events/spring-concert",
        "https://lincoln-high.org/photos/2023/prom",
        "https://lincoln-high.org/sitemap",
        "https://lincoln-high.org/privacy",
        "https://lincoln-high.org/alumni",
    ],
)
def test_deprio_paths_score_negative(url: str):
    assert score_url(url) < 0


def test_neutral_path_scores_zero():
    assert score_url("https://lincoln-high.org/about/history") == 0
    assert score_url("https://lincoln-high.org/programs/spanish") == 0


def test_stafford_not_misclassified_as_staff():
    """Word-boundary tokenization: 'stafford' (a TX town) must not trigger
    the 'staff' boost."""
    assert score_url("https://lincoln-high.org/locations/stafford") == 0


def test_anchor_text_boosts_when_path_neutral():
    """A page at a generic path but linked with `Staff Directory` text
    still gets some lift, just less than a path-token match."""
    url = "https://lincoln-high.org/p/123"
    bare = score_url(url)
    with_anchor = score_url(url, anchor_text="Staff Directory")
    assert with_anchor > bare


def test_anchor_text_deprio_works():
    url = "https://lincoln-high.org/p/123"
    with_anchor = score_url(url, anchor_text="Latest News Archive")
    assert with_anchor < 0


def test_multi_token_path_stacks():
    """A path with multiple matching tokens accumulates."""
    one = score_url("https://x.org/staff/")
    two = score_url("https://x.org/staff/directory/")
    assert two > one


def test_rank_urls_orders_descending_by_score():
    urls = [
        "https://x.org/news/2024",
        "https://x.org/about/staff/",
        "https://x.org/calendar",
        "https://x.org/about/history",
        "https://x.org/faculty/band",
    ]
    ranked = rank_urls(urls)
    # The two highest-scoring URLs (staff, faculty/band) come first; the
    # neutral one comes next; deprios last.
    assert ranked[0] in ("https://x.org/about/staff/", "https://x.org/faculty/band")
    assert ranked[1] in ("https://x.org/about/staff/", "https://x.org/faculty/band")
    assert ranked[2] == "https://x.org/about/history"
    assert set(ranked[3:]) == {
        "https://x.org/news/2024",
        "https://x.org/calendar",
    }


def test_rank_urls_stable_on_ties():
    """Equal-score URLs keep input order — preserves caller's upstream signal."""
    urls = [
        "https://x.org/about/history",
        "https://x.org/programs/spanish",
        "https://x.org/locations/main",
    ]
    assert rank_urls(urls) == urls


def test_rank_urls_uses_anchor_map_when_provided():
    urls = ["https://x.org/p/1", "https://x.org/p/2"]
    anchors = {"https://x.org/p/2": "Faculty Directory"}
    ranked = rank_urls(urls, anchors=anchors)
    # p/2 has anchor boost, p/1 doesn't → p/2 first.
    assert ranked[0] == "https://x.org/p/2"
