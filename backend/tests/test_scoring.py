"""Scoring service tests — pure function, no DB needed."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scoring import ContactScore, score_contact


def _c(**kw) -> SimpleNamespace:
    """Lightweight Contact stand-in — has all the score-relevant fields."""
    return SimpleNamespace(
        title=kw.get("title"),
        email=kw.get("email"),
        email_confidence=kw.get("email_confidence"),
        phone=kw.get("phone"),
        segment_tag=kw.get("segment_tag"),
        evidence_excerpt=kw.get("evidence_excerpt"),
    )


def _o(state: str | None) -> SimpleNamespace:
    return SimpleNamespace(state=state)


def test_role_match_band_director() -> None:
    s = score_contact(_c(title="Band Director"))
    assert s.role_match is True


def test_role_match_case_insensitive() -> None:
    s = score_contact(_c(title="SUPERINTENDENT"))
    assert s.role_match is True


def test_role_match_false_for_unrelated_title() -> None:
    s = score_contact(_c(title="Office Manager"))
    assert s.role_match is False


def test_state_match_requires_target_list() -> None:
    s = score_contact(_c(), organization=_o("TX"), target_states=None)
    assert s.state_match is False  # no list → never True


def test_state_match_against_target_list() -> None:
    s = score_contact(_c(), organization=_o("TX"), target_states=frozenset({"TX", "NY"}))
    assert s.state_match is True
    s = score_contact(_c(), organization=_o("CA"), target_states=frozenset({"TX", "NY"}))
    assert s.state_match is False


def test_state_match_lowercase_normalized() -> None:
    s = score_contact(_c(), organization=_o("tx"), target_states=frozenset({"TX"}))
    assert s.state_match is True


def test_has_direct_email_requires_non_pattern_guess() -> None:
    assert (
        score_contact(_c(email="a@b.com", email_confidence="verified")).has_direct_email is True
    )
    assert (
        score_contact(_c(email="a@b.com", email_confidence="pattern_guess")).has_direct_email
        is False
    )
    assert score_contact(_c(email=None)).has_direct_email is False


def test_has_phone_simple() -> None:
    assert score_contact(_c(phone="(602) 555-0100")).has_phone is True
    assert score_contact(_c(phone=None)).has_phone is False


def test_travel_signal_in_segment_tag() -> None:
    s = score_contact(_c(segment_tag="K-12 global studies"))
    assert s.has_travel_signal is True


def test_travel_signal_in_evidence_excerpt() -> None:
    s = score_contact(
        _c(evidence_excerpt="Leads the annual band performance tour to Disney World")
    )
    assert s.has_travel_signal is True


def test_travel_signal_false_on_neutral_excerpt() -> None:
    s = score_contact(_c(evidence_excerpt="Teaches advanced algebra to juniors and seniors"))
    assert s.has_travel_signal is False


def test_composite_score() -> None:
    s = score_contact(
        _c(
            title="Band Director",
            email="jane@school.org",
            email_confidence="verified",
            phone="(602) 555-0100",
            segment_tag="K-12 band",
            evidence_excerpt="Band Director Jane Smith leads the annual band trip",
        ),
        organization=_o("TX"),
        target_states=frozenset({"TX"}),
    )
    assert s.composite == 5
    assert isinstance(s, ContactScore)
