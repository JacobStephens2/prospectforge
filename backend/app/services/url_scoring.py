"""Score URLs by likely contact-page yield (yield workstream step 4).

The crawl budget is finite. With the run-level cap at 1500 pages, a single
school site easily exhausts the budget on news/calendar/event pages before
reaching `/about/staff/directory`. Scoring orders the frontier so the
high-yield paths win the budget.

Pure URL-path scoring today; anchor-text scoring is plumbed through the
function signature but only kicks in when callers pass it (the link
discovery path could be extended to capture <a> text in a follow-up).

Tuning policy: keep token lists small and conservative. False positives
(boosting a non-contact page) waste budget; false negatives (missing a
real contact page) lose contacts. Add tokens deliberately, with a fixture
to demonstrate the lift.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


# Tokens that suggest a contact-bearing page. Word-boundary matched so
# "staff" doesn't trigger on "stafford" (a real Texas town in our pilot
# market). Matched against the URL path and (when available) anchor text.
_BOOST_TOKENS = (
    "staff",
    "faculty",
    "directory",
    "directories",
    "contact",
    "contacts",
    "team",
    "leadership",
    "administration",
    "administrators",
    "department",
    "departments",
    "coach",
    "coaches",
    "advisor",
    "advisors",
    "principal",
    "dean",
    "deans",
    "superintendent",
    "people",
    "employees",
    "personnel",
    # Acme-specific segment markers — pages titled with these almost always
    # list the relevant teacher / director.
    "band",
    "choir",
    "theater",
    "theatre",
    "drama",
    "music",
    "orchestra",
    "performing-arts",
    "performing_arts",
    "fine-arts",
    "fine_arts",
    "world-language",
    "world_language",
    "global",
)

# Tokens that suggest a non-contact page. Pages matching these tend to be
# editorial / time-sensitive content rather than directory listings.
_DEPRIO_TOKENS = (
    "news",
    "blog",
    "blogs",
    "article",
    "articles",
    "post",
    "posts",
    "story",
    "stories",
    "events",
    "event",
    "calendar",
    "schedule",
    "gallery",
    "galleries",
    "photo",
    "photos",
    "video",
    "videos",
    "media",
    "image",
    "images",
    "archive",
    "archives",
    "press",
    "press-release",
    "alumni",
    "donate",
    "donation",
    "shop",
    "store",
    "merchandise",
    "cart",
    "checkout",
    "privacy",
    "terms",
    "cookie",
    "accessibility",
    "sitemap",
    "rss",
    "feed",
)


_BOOST_PATH_WEIGHT = 10
_DEPRIO_PATH_WEIGHT = -5
_BOOST_ANCHOR_WEIGHT = 5
_DEPRIO_ANCHOR_WEIGHT = -3


# Word-boundary regex compiled once. URL paths use '-', '_', '/' as
# delimiters, so we split on any non-alpha character before scanning.
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokenize(s: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT_RE.split(s.lower()) if t}


def score_url(url: str, anchor_text: str | None = None) -> int:
    """Return an integer score for the candidate URL.

    Higher is better. Zero is neutral. Negative scores are valid — they
    indicate a likely-low-yield page that should be visited last (or
    skipped if the budget runs out).

    Anchor text is optional. When supplied, its tokens contribute at a
    lower weight than path tokens — paths are more reliable signal than
    link text written by humans.
    """
    parsed = urlparse(url)
    path_tokens = _tokenize(parsed.path)
    score = 0
    for tok in _BOOST_TOKENS:
        if tok in path_tokens or (
            "-" in tok and tok.replace("-", "") in path_tokens
        ) or (
            "_" in tok and tok.replace("_", "") in path_tokens
        ):
            score += _BOOST_PATH_WEIGHT
    for tok in _DEPRIO_TOKENS:
        if tok in path_tokens:
            score += _DEPRIO_PATH_WEIGHT

    if anchor_text:
        anchor_tokens = _tokenize(anchor_text)
        for tok in _BOOST_TOKENS:
            if tok in anchor_tokens:
                score += _BOOST_ANCHOR_WEIGHT
        for tok in _DEPRIO_TOKENS:
            if tok in anchor_tokens:
                score += _DEPRIO_ANCHOR_WEIGHT

    return score


def rank_urls(
    urls: list[str],
    *,
    anchors: dict[str, str] | None = None,
) -> list[str]:
    """Stable-sort URLs by descending score. Ties keep original order so
    callers retain whatever signal their upstream produced.

    `anchors` is an optional {url: anchor_text} map. URLs without an
    anchor entry are scored on path alone.
    """
    anchors = anchors or {}
    # Decorate-sort-undecorate to preserve insertion order on ties.
    indexed = [
        (-score_url(u, anchors.get(u)), idx, u) for idx, u in enumerate(urls)
    ]
    indexed.sort()
    return [u for _score, _idx, u in indexed]
