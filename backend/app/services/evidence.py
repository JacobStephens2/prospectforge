"""Evidence-excerpt verifier — spec §5.3 + §7.

Every Contact must carry an `evidence_excerpt` that the LLM claims appears in
the source page. The verifier proves (or disproves) that claim before the row
is allowed through. Three outcomes:

  verified  — exact substring match, case-insensitive, whitespace-normalized
  fuzzy     — partial substring of ≥ 0.9 similarity against any source window
  failed    — neither; row is downgraded to needs_review and never auto-exported

The Hard data rule (spec §5.3): "Evidence excerpt must verify. Non-verifying
rows are rejected or flagged needs_review; never auto-exported." This module
is the function that enforces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_FUZZY_THRESHOLD = 0.9
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class EvidenceCheck:
    """Result of verifying one excerpt against one source text."""

    status: str          # "verified" | "fuzzy" | "failed"
    score: float | None  # similarity in [0,1]; None on exact match


def _normalize(text: str) -> str:
    """Collapse whitespace + lowercase for resilient matching.

    LLMs often re-emit copied text with re-flowed whitespace (line breaks
    become spaces, etc.). Normalizing both sides closes that gap without
    weakening the match — punctuation and word identity are preserved.
    """
    return _WHITESPACE.sub(" ", text).strip().lower()


def verify_excerpt(excerpt: str | None, source_text: str | None) -> EvidenceCheck:
    """Decide whether `excerpt` is supported by `source_text`."""
    if not excerpt or not excerpt.strip():
        return EvidenceCheck("failed", None)
    if not source_text:
        return EvidenceCheck("failed", None)

    norm_excerpt = _normalize(excerpt)
    norm_source = _normalize(source_text)

    if not norm_excerpt:
        return EvidenceCheck("failed", None)

    # 1) Exact substring after normalization → verified.
    if norm_excerpt in norm_source:
        return EvidenceCheck("verified", 1.0)

    # 2) Fuzzy: compute coverage as the sum of ALL non-overlapping matching
    # blocks divided by excerpt length. This handles the common case where
    # the LLM rephrases one or two tokens (e.g. drops a colon, changes a
    # comma) inside an otherwise-verbatim excerpt — the matching blocks on
    # either side of the edit still cover ~all of the excerpt.
    #
    # `get_matching_blocks()` returns non-overlapping blocks plus a (0,0,0)
    # sentinel at the end. `autojunk=False` keeps SequenceMatcher from
    # treating common characters as junk on long inputs, which would
    # otherwise depress coverage for the typical evidence/source pair.
    matcher = SequenceMatcher(None, norm_source, norm_excerpt, autojunk=False)
    matched_chars = sum(b.size for b in matcher.get_matching_blocks())
    coverage = matched_chars / len(norm_excerpt) if norm_excerpt else 0.0
    # ratio() over the whole strings is a sanity backstop; coverage is what
    # we actually rely on. max() handles the edge case where the excerpt is
    # short and the source is also short.
    best = max(coverage, matcher.ratio())

    if best >= _FUZZY_THRESHOLD:
        return EvidenceCheck("fuzzy", best)
    return EvidenceCheck("failed", best)
