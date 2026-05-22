"""SPA shell detection + auto-escalation gating tests."""

from __future__ import annotations

import pytest

from app.services.spa_detect import (
    EMPTY_MARKDOWN_THRESHOLD,
    looks_like_spa_shell,
    should_auto_escalate,
)


# ---------- looks_like_spa_shell ----------


def test_spa_shell_react_mount_point():
    html = b'<!DOCTYPE html><html><body><div id="root"></div></body></html>'
    assert looks_like_spa_shell(html, markdown=None) is True


def test_spa_shell_finalsite():
    html = b"<html><body><script src='/cdn/finalsite/app.js'></script></body></html>"
    assert looks_like_spa_shell(html, markdown="") is True


def test_spa_shell_next_data():
    html = b'<html><body><script id="__NEXT_DATA__">{}</script></body></html>'
    assert looks_like_spa_shell(html, markdown=None) is True


def test_no_spa_shell_when_markdown_already_rich():
    """Even if SPA markers exist, a page that produced plenty of text is
    not a shell — server-rendered or hybrid SSR/CSR."""
    html = b'<html><body><div id="root">SSR-rendered content here</div></body></html>'
    md = "a" * (EMPTY_MARKDOWN_THRESHOLD + 100)
    assert looks_like_spa_shell(html, markdown=md) is False


def test_no_spa_shell_when_no_markers():
    html = b"<html><body><p>plain static page, no JS framework</p></body></html>"
    assert looks_like_spa_shell(html, markdown=None) is False


def test_no_spa_shell_when_body_empty():
    assert looks_like_spa_shell(None, markdown=None) is False
    assert looks_like_spa_shell(b"", markdown=None) is False


def test_spa_shell_marker_is_case_insensitive():
    html = b'<html><body><div ID="ROOT"></div></body></html>'
    assert looks_like_spa_shell(html, markdown=None) is True


# ---------- should_auto_escalate ----------


_SPA_HTML = b'<html><body><div id="root"></div></body></html>'


def test_escalate_when_all_conditions_met():
    assert should_auto_escalate(
        url="https://x.test/staff/directory",
        raw_body=_SPA_HTML,
        markdown=None,
        escalations_used=0,
    )


def test_no_escalate_when_url_score_zero():
    """Don't burn Firecrawl credits re-fetching low-score paths even if
    they smell like SPAs — likely a privacy page or generic component."""
    assert not should_auto_escalate(
        url="https://x.test/privacy",
        raw_body=_SPA_HTML,
        markdown=None,
        escalations_used=0,
    )


def test_no_escalate_when_neutral_path():
    """Neutral paths (no boost tokens, no deprio tokens) — same call.
    We require a positive signal that this is a contact-bearing page."""
    assert not should_auto_escalate(
        url="https://x.test/about/history",
        raw_body=_SPA_HTML,
        markdown=None,
        escalations_used=0,
    )


def test_no_escalate_when_budget_exhausted():
    assert not should_auto_escalate(
        url="https://x.test/staff",
        raw_body=_SPA_HTML,
        markdown=None,
        escalations_used=25,
        max_escalations=25,
    )


def test_no_escalate_when_static_page():
    """Static page with content — Tier-1 was fine."""
    md = "a" * (EMPTY_MARKDOWN_THRESHOLD + 200)
    assert not should_auto_escalate(
        url="https://x.test/staff",
        raw_body=b"<html><body><p>plain</p></body></html>",
        markdown=md,
        escalations_used=0,
    )
