"""Crawl worker tests: safe_fetch + crawl_url end-to-end.

These exercise the SSRF guard at the fetch/redirect boundary using httpx's
MockTransport — no real network. The DB integration test (test_crawl_url_persists)
creates a real prospect_run + source + user against the test postgres.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ProspectRun, Source, SourcePage, User
from app.workers.crawl import crawl_url, safe_fetch


def _client(handler) -> httpx.Client:
    """Build a Client backed by a MockTransport with the given handler."""
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def _public_ip_patch():
    """Force every DNS resolution in ssrf_guard to return a public IP so the
    transport-level mock is the only failure surface."""
    return patch("app.services.ssrf_guard._resolve_all", return_value=("142.250.190.78",))


# ---------- safe_fetch ----------


def test_safe_fetch_basic_200() -> None:
    body = b"<html><body><h1>hello</h1><p>some content here</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    with _public_ip_patch():
        result = safe_fetch("https://example.com/", client=_client(handler))

    assert result.status == "fetched"
    assert result.http_status == 200
    assert result.content_hash and len(result.content_hash) == 64
    assert result.content_markdown is not None
    assert result.page_metadata["content_type"] == "text/html"
    assert result.page_metadata["redirect_chain"] == []


def test_safe_fetch_follows_redirects_and_revalidates() -> None:
    """A 302 to another public URL gets re-validated and then fetched."""
    visited: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/end"})
        return httpx.Response(200, content=b"<p>done</p>", headers={"content-type": "text/html"})

    with _public_ip_patch():
        result = safe_fetch("https://example.com/start", client=_client(handler))

    assert result.status == "fetched"
    assert result.page_metadata["redirect_chain"] == ["https://example.com/start"]
    assert visited == ["https://example.com/start", "https://example.com/end"]


def test_safe_fetch_blocks_redirect_to_private_ip() -> None:
    """Spec §6.3 DNS rebinding guard: a redirect target must be re-validated.
    A 302 to a hostname that resolves to a private IP is blocked at the
    redirect, not after data leaks."""
    call_count = {"n": 0}

    def fake_resolve(host: str):
        call_count["n"] += 1
        # First lookup (the initial URL) → public; second lookup (redirect target) → private
        return ("142.250.190.78",) if call_count["n"] == 1 else ("10.0.0.5",)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://internal.example.com/admin"})
        # If we get here, the SSRF guard failed — the test should never reach it.
        return httpx.Response(200, content=b"LEAKED")

    with patch("app.services.ssrf_guard._resolve_all", side_effect=fake_resolve):
        result = safe_fetch("https://example.com/start", client=_client(handler))

    assert result.status == "blocked_ssrf"
    assert result.page_metadata["ssrf_code"] == "blocked_ip"
    assert result.page_metadata["blocked_at"] == "https://internal.example.com/admin"
    # The redirect chain was recorded up to the block point.
    assert result.page_metadata["redirect_chain"] == ["https://example.com/start"]


def test_safe_fetch_blocks_file_scheme_redirect() -> None:
    """A 302 to file:// is rejected by scheme validation, not by IP check."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    with _public_ip_patch():
        result = safe_fetch("https://example.com/start", client=_client(handler))

    assert result.status == "blocked_ssrf"
    assert result.page_metadata["ssrf_code"] == "bad_scheme"


def test_safe_fetch_redirect_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    with _public_ip_patch():
        result = safe_fetch("https://example.com/loop", client=_client(handler))

    assert result.status == "blocked_redirect_limit"


def test_safe_fetch_http_error_is_recorded_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    with _public_ip_patch():
        result = safe_fetch("https://example.com/", client=_client(handler))

    assert result.status == "error"
    assert result.error and "ConnectError" in result.error


def test_safe_fetch_detects_contact_form_on_html_page() -> None:
    """When a fetched HTML body contains a contact-style form, safe_fetch
    records has_contact_form + the form's action target on page_metadata.
    This is the "reachable via form" signal for orgs that don't publish
    individual emails."""
    body = (
        b"<html><body>"
        b"<form action=\"/contact/submit\" class=\"contact-form\">"
        b"<input type=\"email\" name=\"email\">"
        b"<textarea name=\"message\"></textarea>"
        b"<button>Send</button>"
        b"</form>"
        b"</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    with _public_ip_patch():
        result = safe_fetch("https://school.example.com/contact", client=_client(handler))

    assert result.status == "fetched"
    assert result.page_metadata.get("has_contact_form") is True
    assert "/contact/submit" in result.page_metadata.get("contact_form_actions", [])


def test_safe_fetch_no_contact_form_metadata_when_html_has_no_form() -> None:
    """A plain HTML page without any form leaves has_contact_form unset
    (we don't want noise keys on every fetched page)."""
    body = b"<html><body><p>Just text, no forms.</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    with _public_ip_patch():
        result = safe_fetch("https://example.com/", client=_client(handler))

    assert "has_contact_form" not in result.page_metadata
    assert "contact_form_actions" not in result.page_metadata


def test_safe_fetch_initial_ssrf_block_is_recorded() -> None:
    """First-hop SSRF block (e.g. user paste a private URL directly)."""
    with patch("app.services.ssrf_guard._resolve_all", return_value=("10.0.0.5",)):
        result = safe_fetch("https://attacker.example.com/")
    assert result.status == "blocked_ssrf"
    assert result.page_metadata["blocked_at"] == "https://attacker.example.com/"
    assert result.page_metadata["redirect_chain"] == []


# ---------- crawl_url DB integration ----------


@pytest.fixture()
def fixture_run() -> uuid.UUID:
    """Create a real user + source + run so crawl_url has FKs to satisfy."""
    db = SessionLocal()
    try:
        user = User(email=f"test-{uuid.uuid4()}@acme-travel.example", full_name="Test", role="admin")
        db.add(user)
        db.flush()
        source = Source(name="Test Source", url="https://example.com/", created_by=user.id)
        db.add(source)
        db.flush()
        run = ProspectRun(
            source_id=source.id,
            created_by=user.id,
            max_cost_cents=500,
            budget_approved_by=user.id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    return run_id


def test_crawl_url_persists_a_source_page(fixture_run: uuid.UUID) -> None:
    """Block an obvious SSRF target and verify the DB row exists with the
    right shape. We use a SSRF block to avoid mocking httpx in this path."""
    with patch("app.services.ssrf_guard._resolve_all", return_value=("10.0.0.5",)):
        out = crawl_url(str(fixture_run), "https://internal.example.com/")

    assert out["status"] == "blocked_ssrf"
    assert out["http_status"] is None

    db = SessionLocal()
    try:
        rows = db.execute(select(SourcePage).where(SourcePage.run_id == fixture_run)).scalars().all()
        assert len(rows) == 1
        page = rows[0]
        assert page.status == "blocked_ssrf"
        assert page.url == "https://internal.example.com/"
        assert page.page_metadata is not None
        assert page.page_metadata["ssrf_code"] == "blocked_ip"
    finally:
        db.close()
