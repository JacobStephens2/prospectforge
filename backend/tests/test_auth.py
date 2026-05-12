"""Auth flow tests — request, verify, logout, me.

The mailer is monkeypatched so we don't burn Mandrill quota; the link the
mailer would have sent is captured by the patch and used to drive /verify.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import AuthSession, MagicLinkToken, User
from app.routes.auth import COOKIE_NAME
from app.services import mailer as mailer_mod


@pytest.fixture(autouse=True)
def _dev_mode_cookies(monkeypatch):
    """TestClient uses http:// so Secure cookies don't round-trip. Force
    development mode for the auth route so cookies are set without Secure."""
    monkeypatch.setattr(settings, "app_env", "development")


@pytest.fixture
def captured_mail(monkeypatch):
    """Replace send_magic_link; record every call."""
    sent: list[dict] = []

    def fake_send_magic_link(*, to: str, link: str, expires_minutes: int = 15) -> None:
        sent.append({"to": to, "link": link, "expires_minutes": expires_minutes})

    monkeypatch.setattr(mailer_mod, "send_magic_link", fake_send_magic_link)
    # Also patch the import inside services.auth — that's where it's actually called.
    from app.services import auth as auth_svc

    # auth_svc does `from app.services.mailer import send_magic_link` lazily
    # inside the function, so patching the module is enough.
    return sent


@pytest.fixture
def whitelisted_user():
    """A real User row (the whitelist). Different from seeded_user — this one
    we want to exercise the real auth flow against."""
    db = SessionLocal()
    try:
        u = User(
            email=f"wl-{uuid.uuid4()}@acme-travel.example",
            full_name="Whitelisted",
            role="admin",
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u
    finally:
        db.close()


def test_request_known_email_sends_mail(captured_mail, whitelisted_user) -> None:
    c = TestClient(app)
    r = c.post("/api/auth/request", json={"email": whitelisted_user.email})
    assert r.status_code == 204
    assert len(captured_mail) == 1
    assert captured_mail[0]["to"] == whitelisted_user.email
    assert "/verify?token=" in captured_mail[0]["link"]


def test_request_unknown_email_still_204_but_no_send(captured_mail) -> None:
    """Whitelist invisible from the outside: same 204 either way."""
    c = TestClient(app)
    # Use a real-format email (some validators reject .test TLD).
    r = c.post(
        "/api/auth/request",
        json={"email": f"ghost-{uuid.uuid4()}@example.com"},
    )
    assert r.status_code == 204
    assert captured_mail == []


def test_request_inactive_user_treated_as_unknown(captured_mail) -> None:
    db = SessionLocal()
    try:
        u = User(
            email=f"inactive-{uuid.uuid4()}@acme-travel.example",
            full_name="Inactive",
            role="admin",
            active=False,
        )
        db.add(u)
        db.commit()
        email = u.email
    finally:
        db.close()
    c = TestClient(app)
    r = c.post("/api/auth/request", json={"email": email})
    assert r.status_code == 204
    assert captured_mail == []


def test_request_malformed_email_422() -> None:
    c = TestClient(app)
    r = c.post("/api/auth/request", json={"email": "not-an-email"})
    assert r.status_code == 422


def test_verify_happy_path_sets_cookie(captured_mail, whitelisted_user) -> None:
    c = TestClient(app)
    c.post("/api/auth/request", json={"email": whitelisted_user.email})
    link = captured_mail[0]["link"]
    token = link.split("token=", 1)[1]

    r = c.post(f"/api/auth/verify?token={token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == whitelisted_user.email
    assert COOKIE_NAME in c.cookies
    # Subsequent /me with the cookie now resolves.
    r = c.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == whitelisted_user.email


def test_verify_unknown_token_410() -> None:
    c = TestClient(app)
    r = c.post(f"/api/auth/verify?token={'x' * 50}")
    assert r.status_code == 410


def test_verify_consumed_token_410(captured_mail, whitelisted_user) -> None:
    c = TestClient(app)
    c.post("/api/auth/request", json={"email": whitelisted_user.email})
    token = captured_mail[0]["link"].split("token=", 1)[1]
    r1 = c.post(f"/api/auth/verify?token={token}")
    assert r1.status_code == 200
    r2 = c.post(f"/api/auth/verify?token={token}")
    assert r2.status_code == 410


def test_verify_expired_token_410(captured_mail, whitelisted_user) -> None:
    c = TestClient(app)
    c.post("/api/auth/request", json={"email": whitelisted_user.email})
    token = captured_mail[0]["link"].split("token=", 1)[1]
    # Backdate the token's expiry.
    db = SessionLocal()
    try:
        row = db.query(MagicLinkToken).filter(MagicLinkToken.token == token).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    r = c.post(f"/api/auth/verify?token={token}")
    assert r.status_code == 410


def test_logout_revokes_session(captured_mail, whitelisted_user) -> None:
    c = TestClient(app)
    c.post("/api/auth/request", json={"email": whitelisted_user.email})
    token = captured_mail[0]["link"].split("token=", 1)[1]
    c.post(f"/api/auth/verify?token={token}")
    assert c.get("/api/auth/me").status_code == 200
    r = c.post("/api/auth/logout")
    assert r.status_code == 204
    # The TestClient drops the cookie now, so /me 401s.
    assert c.get("/api/auth/me").status_code == 401


def test_me_without_session_401() -> None:
    c = TestClient(app)
    assert c.get("/api/auth/me").status_code == 401


def test_three_whitelisted_admins_seeded() -> None:
    """Migration 0005 must have created Jacob/Sam/Morgan as active admins."""
    db = SessionLocal()
    try:
        for email in (
            "jstephens@acme-travel.example",
            "srivers@acme-travel.example",
            "mlee@acme-travel.example",
        ):
            u = db.query(User).filter(User.email == email).first()
            assert u is not None, f"missing seed user {email}"
            assert u.active is True
            assert u.role == "admin"
    finally:
        db.close()
