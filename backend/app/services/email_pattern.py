"""Email-pattern inference (yield workstream — enrichment).

Once we have >=2 verified emails on a domain, the local-part pattern is
usually obvious (jdoe@, john.doe@, johnd@, ...). Given a new person's
name at that domain, we can synthesize the most likely address. This is
the lever that turns "we found Tyler Butler and Michelle Halvorsen's
emails at hillcrestacademy.example" into "...so Anna's is probably
akowal-byrne@hillcrestacademy.example".

CRITICAL SAFETY: an inferred email is a GUESS. It must be validated
(services.email_validate) before it's treated as sendable. The Perplexity
Model Council flagged that K-12 domains are frequently catch-all, so an
inferred address can pass SMTP without the mailbox existing. Callers must
gate on validation and label confidence accordingly — never emit an
inferred email as 'verified'.

We require a MINIMUM number of agreeing samples before trusting a pattern
(MIN_SAMPLES). With one sample the "pattern" is just that one person's
idiosyncratic address; two agreeing samples is the floor, and the council's
conservative guidance (5-10) can be dialed in via the caller.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


MIN_SAMPLES = 2


@dataclass(frozen=True)
class NameParts:
    first: str
    last: str

    @property
    def f(self) -> str:
        return self.first[:1] if self.first else ""

    @property
    def l(self) -> str:  # noqa: E743 - single-letter is the domain term here
        return self.last[:1] if self.last else ""


def split_name(name: str) -> NameParts | None:
    """Parse 'First [Middle...] Last' into first + last. Returns None when
    we can't get two name tokens (single mononym, empty). Honorifics and
    suffixes are stripped. Hyphenated surnames are preserved as one token."""
    if not name:
        return None
    cleaned = re.sub(
        r"\b(dr|mr|mrs|ms|mx|prof|professor|rev|sir|dame|jr|sr|ii|iii|iv|phd|md|edd)\.?\b",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    )
    # Keep letters, spaces, hyphens, apostrophes; drop the rest.
    cleaned = re.sub(r"[^a-zA-Z\s'\-]", " ", cleaned)
    tokens = [t for t in cleaned.split() if t]
    if len(tokens) < 2:
        return None
    return NameParts(first=tokens[0].lower(), last=tokens[-1].lower())


def _normalize_local(local: str) -> str:
    """Lowercase; strip a trailing +tag and surrounding noise."""
    return local.split("+", 1)[0].strip().lower()


# Each pattern is (name, fn) where fn(NameParts) -> local-part. Order
# matters only for tie-breaking determinism, not correctness.
def _pat_first_last(n: NameParts) -> str:
    return f"{n.first}.{n.last}"


def _pat_firstlast(n: NameParts) -> str:
    return f"{n.first}{n.last}"


def _pat_flast(n: NameParts) -> str:
    return f"{n.f}{n.last}"


def _pat_firstl(n: NameParts) -> str:
    return f"{n.first}{n.l}"


def _pat_first(n: NameParts) -> str:
    return n.first


def _pat_last(n: NameParts) -> str:
    return n.last


def _pat_f_dot_last(n: NameParts) -> str:
    return f"{n.f}.{n.last}"


def _pat_last_first(n: NameParts) -> str:
    return f"{n.last}.{n.first}"


_PATTERNS: tuple[tuple[str, "callable"], ...] = (
    ("first.last", _pat_first_last),
    ("firstlast", _pat_firstlast),
    ("flast", _pat_flast),
    ("firstl", _pat_firstl),
    ("f.last", _pat_f_dot_last),
    ("last.first", _pat_last_first),
    ("first", _pat_first),
    ("last", _pat_last),
)
_PATTERN_BY_NAME = {name: fn for name, fn in _PATTERNS}


@dataclass(frozen=True)
class InferredPattern:
    pattern: str          # e.g. "first.last"
    domain: str
    sample_size: int      # how many (name,email) pairs agreed
    confidence: float     # agreeing / total samples considered


def infer_pattern(
    samples: list[tuple[str, str]],
    *,
    min_samples: int = MIN_SAMPLES,
) -> InferredPattern | None:
    """Given (full_name, email) pairs known to be correct, infer the
    domain's email pattern. All emails must share a domain; pairs whose
    local-part doesn't match any known pattern (role accounts like
    'admissions@', odd formats) are ignored as noise.

    Returns the winning pattern when at least `min_samples` pairs agree
    on it, else None.
    """
    domain: str | None = None
    votes: Counter[str] = Counter()
    usable = 0
    for name, email in samples:
        if not email or "@" not in email:
            continue
        local, _, dom = email.partition("@")
        dom = dom.strip().lower()
        local = _normalize_local(local)
        if domain is None:
            domain = dom
        elif dom != domain:
            # Mixed domains — not a single-domain inference. Skip the
            # off-domain pair rather than abort, so one stray row doesn't
            # kill an otherwise-clean signal.
            continue
        parts = split_name(name)
        if parts is None:
            continue
        usable += 1
        for pat_name, fn in _PATTERNS:
            try:
                if fn(parts) == local:
                    votes[pat_name] += 1
            except Exception:  # noqa: BLE001
                continue

    if domain is None or not votes:
        return None
    winner, count = votes.most_common(1)[0]
    if count < min_samples:
        return None
    confidence = count / usable if usable else 0.0
    return InferredPattern(
        pattern=winner,
        domain=domain,
        sample_size=count,
        confidence=confidence,
    )


def generate_email(name: str, inferred: InferredPattern) -> str | None:
    """Apply an inferred pattern to a new name → candidate email. Returns
    None when the name can't be parsed into first+last."""
    parts = split_name(name)
    if parts is None:
        return None
    fn = _PATTERN_BY_NAME.get(inferred.pattern)
    if fn is None:
        return None
    local = fn(parts)
    if not local:
        return None
    return f"{local}@{inferred.domain}"
