"""Scoring service — pure functions over a Contact + its Organization.

Spec §9 keeps it deliberately simple for v1: 5 boolean flags exposed as
sortable columns in the review queue. No YAML weight tuning, no
source_quality_score feedback loop — those arrive with outcomes data in
v1.1 once we have real approval-rate signal from a pilot.

Heuristics:
  role_match          — title contains an Acme-relevant role keyword
  state_match         — organization.state is in the target-state list
                        (target list is a Week-0 blocker; we default to
                        empty, which leaves this False until a list lands)
  has_direct_email    — non-null email AND not pattern_guess
  has_phone           — non-null phone
  has_travel_signal   — title/segment_tag/excerpt mention travel/trip vocab

These are *pure* over the inputs — call them again any time without DB
side effects. The worker writes the booleans back to the Contact row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Contact, Organization


# Roles Acme's sales team wants to reach. Match any of these as substrings
# (case-insensitive) in the title. Sources: Morgan's segment vocab + the
# CRM tblAgentUsers.Title sample.
_ROLE_KEYWORDS = (
    "director",            # band/choir/theater director, activities director, athletic director
    "teacher",             # social studies/world language/etc.
    "coach",               # speech, debate, robotics, sports
    "chair",               # department chair
    "head of",             # head of school, head of upper school
    "principal",
    "superintendent",
    "dean",
    "faculty",
    "advisor",             # student council/club advisor
    "coordinator",         # travel/activities coordinator
    "trip leader",
    "group leader",
)


# Words that signal the page is about travel — feeds the has_travel_signal
# flag. Same list as the §6.4 chunking keywords; deliberately kept simple.
_TRAVEL_KEYWORDS = (
    "travel", "trip", "tour", "tours", "destination",
    "abroad", "international", "global", "world",
    "study away", "exchange", "performance tour",
    "field trip", "educational travel",
)


@dataclass(frozen=True)
class ContactScore:
    """Decomposed score — useful for tests + the review UI's evidence drawer."""

    role_match: bool
    state_match: bool
    has_direct_email: bool
    has_phone: bool
    has_travel_signal: bool

    @property
    def composite(self) -> int:
        """0–5 count of true flags. Surface this in the queue, sort by it."""
        return sum(
            (
                self.role_match,
                self.state_match,
                self.has_direct_email,
                self.has_phone,
                self.has_travel_signal,
            )
        )


def _contains_any(text: str | None, needles: tuple[str, ...]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(n in lower for n in needles)


def score_contact(
    contact: Contact,
    *,
    organization: Organization | None = None,
    target_states: frozenset[str] | None = None,
) -> ContactScore:
    """Compute the 5 flags. `target_states` is the spec §22 Week-0 blocker
    list — pass None / empty until Morgan confirms which states Acme prioritizes,
    and state_match will be False everywhere."""
    title_signal = _contains_any(contact.title, _ROLE_KEYWORDS)

    state_match = False
    if target_states and organization and organization.state:
        state_match = organization.state.strip().upper() in target_states

    has_direct_email = bool(contact.email) and contact.email_confidence != "pattern_guess"
    has_phone = bool(contact.phone)

    travel_signal = (
        _contains_any(contact.segment_tag, _TRAVEL_KEYWORDS)
        or _contains_any(contact.title, _TRAVEL_KEYWORDS)
        or _contains_any(contact.evidence_excerpt, _TRAVEL_KEYWORDS)
    )

    return ContactScore(
        role_match=title_signal,
        state_match=state_match,
        has_direct_email=has_direct_email,
        has_phone=has_phone,
        has_travel_signal=travel_signal,
    )


def apply_score(contact: Contact, score: ContactScore) -> None:
    """Write the boolean fields to the contact in-memory. Caller commits."""
    contact.score_role_match = score.role_match
    contact.score_state_match = score.state_match
    contact.score_has_direct_email = score.has_direct_email
    contact.score_has_phone = score.has_phone
    contact.score_has_travel_signal = score.has_travel_signal
