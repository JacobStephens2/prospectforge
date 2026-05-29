"""Contact-form detection heuristics."""

from __future__ import annotations

from app.services.contact_form_detect import (
    detect_contact_form_actions,
    has_contact_form,
)


def test_detects_email_input_plus_textarea():
    html = """
    <form action="/contact/submit">
      <input type="email" name="email">
      <textarea name="message"></textarea>
      <button>Send</button>
    </form>
    """
    assert has_contact_form(html) is True


def test_detects_textarea_only_form():
    html = '<form><textarea name="msg"></textarea></form>'
    assert has_contact_form(html) is True


def test_detects_email_input_with_contact_hint():
    html = """
    <form class="contact-form">
      <input type="email" name="your_email">
      <input type="submit">
    </form>
    """
    assert has_contact_form(html) is True


def test_named_email_field_without_type():
    html = """
    <form id="get-in-touch">
      <input name="Email" type="text">
      <textarea></textarea>
    </form>
    """
    assert has_contact_form(html) is True


def test_newsletter_signup_email_only_no_hint_not_detected():
    """A bare email field with no message box and no contact hint is more
    likely a newsletter signup — we don't flag it."""
    html = '<form action="/subscribe"><input type="email" name="email"></form>'
    assert has_contact_form(html) is False


def test_no_form_returns_false():
    assert has_contact_form("<div>no forms here</div>") is False
    assert has_contact_form("") is False
    assert has_contact_form(None) is False


def test_detect_actions_returns_contact_form_targets():
    html = """
    <form action="/contact/submit"><textarea></textarea></form>
    <form action="/subscribe"><input type="email" name="email"></form>
    """
    actions = detect_contact_form_actions(html)
    assert "/contact/submit" in actions
    # The subscribe form (email-only, no hint) isn't contact-ish → excluded.
    assert "/subscribe" not in actions


def test_detect_actions_reveals_mailto():
    html = '<form action="mailto:office@school.org"><textarea></textarea></form>'
    actions = detect_contact_form_actions(html)
    assert "mailto:office@school.org" in actions
