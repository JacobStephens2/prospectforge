"""Staff-directory page detection (yield workstream step 7).

NCES/FCSM 2023 "A Generic and Automated Staff Scraping Tool for School
Webpages" (Alaoui & Hunter-Zinck) ranks candidate pages by string
similarity against a curated list of directory-page phrases. At a
similarity threshold of ~0.9 the tool captures >90% of real directory
pages with ~10% false positive rate. We adapt that approach here.

A page is "likely a staff directory" if any of these match strongly:
  - its <title>
  - the anchor text that linked to it (when known)
  - a short prefix of its rendered text

We use SequenceMatcher.ratio() rather than a heavier NLP similarity
because the candidate phrases are short and the comparison is one-shot
per candidate; spaCy or similar would be overkill and add 300MB of
deps.

Caller integration: the orchestrator can use this to flag pages for
extra-careful handling (extraction confidence, pagination follow,
DOM-motif extraction in a future iteration) without changing the
default extraction path.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher


# Curated list — adapted from the NCES paper Table on slide 9 plus a
# few additions for Acme's segment markers. Order doesn't matter; we
# take the max similarity across the whole list.
_DIRECTORY_PHRASES = (
    "staff directory",
    "faculty directory",
    "faculty and staff",
    "faculty & staff",
    "our staff",
    "our faculty",
    "our teachers",
    "teachers",
    "staff",
    "faculty",
    "meet our team",
    "meet the team",
    "school staff",
    "school faculty",
    "administration",
    "leadership team",
    "department",
    # Acme-specific
    "band staff",
    "choir staff",
    "performing arts staff",
    "theater staff",
    "theatre staff",
    "music department",
    "world languages",
    "global studies",
)

# Match the NCES paper's recommended threshold. Tunable per source if
# false-positive rate becomes a problem on real corpora.
DEFAULT_SIMILARITY_THRESHOLD = 0.9


@dataclass(frozen=True)
class DirectoryScore:
    """Result of one directory-page check."""

    score: float                  # 0.0..1.0, max similarity across phrases
    matched_phrase: str | None    # the phrase that produced `score`
    matched_field: str | None     # title | anchor | body — which input matched

    @property
    def is_directory(self) -> bool:
        return self.score >= DEFAULT_SIMILARITY_THRESHOLD


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_match(text: str) -> tuple[float, str | None]:
    """Return (best_similarity, matching_phrase) for `text` against the
    curated phrase list. Empty/None text returns (0, None)."""
    if not text:
        return 0.0, None
    normalized = " ".join(text.lower().split())
    if not normalized:
        return 0.0, None
    best = 0.0
    best_phrase: str | None = None
    for phrase in _DIRECTORY_PHRASES:
        # Full-string ratio is meaningful for short titles/anchors.
        full = _ratio(normalized, phrase)
        # Also probe substring fit: if the phrase appears anywhere in the
        # text, that's a strong positive even when the text is long.
        substr = 1.0 if phrase in normalized else 0.0
        r = max(full, substr)
        if r > best:
            best = r
            best_phrase = phrase
    return best, best_phrase


def score_directory_likelihood(
    *,
    title: str | None = None,
    anchor_text: str | None = None,
    body_prefix: str | None = None,
    body_prefix_chars: int = 500,
) -> DirectoryScore:
    """Compute the strongest directory-likelihood signal across title,
    anchor, and a short body prefix. The body is sampled (default 500
    chars) rather than scanned in full — directory pages put the heading
    near the top and scanning the entire page invites false positives
    from in-content mentions like "see the staff directory page".
    """
    candidates: list[tuple[float, str | None, str]] = []

    if title:
        s, p = _best_match(title)
        candidates.append((s, p, "title"))
    if anchor_text:
        s, p = _best_match(anchor_text)
        candidates.append((s, p, "anchor"))
    if body_prefix:
        s, p = _best_match(body_prefix[:body_prefix_chars])
        candidates.append((s, p, "body"))

    if not candidates:
        return DirectoryScore(score=0.0, matched_phrase=None, matched_field=None)

    candidates.sort(key=lambda t: t[0], reverse=True)
    best_score, best_phrase, best_field = candidates[0]
    return DirectoryScore(
        score=best_score,
        matched_phrase=best_phrase,
        matched_field=best_field if best_score > 0 else None,
    )
