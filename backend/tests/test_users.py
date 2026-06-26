"""User-management (access whitelist) tests.

These endpoints are admin-only. We mint sessions directly (insert an
AuthSession row + set the cookie) rather than driving the magic-link flow,
which test_auth.py already covers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import AuthSession, User
from app.routes.auth import COOKIE_NAME


def _mk_user(role: str = "admin", active: bool = True) -> User:
    db = SessionLocal()
    try:
        u = User(
            email=f"u-{uuid.uuid4()}@acme-travel.example",
            full_name="Test User",
            role=role,
            active=active,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u
    finally:
        db.close()


def _client_for(user: User) -> TestClient:
    """A TestClient carrying a valid session cookie for `user`."""
    token = f"sess-{uuid.uuid4().hex}{uuid.uuid4().hex}"
    db = SessionLocal()
    try:
        db.add(
            AuthSession(
                user_id=user.id,
                token=token,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.commit()
    finally:
        db.close()
    c = TestClient(app)
    c.cookies.set(COOKIE_NAME, token)
    return c


def test_list_requires_auth() -> None:
    assert TestClient(app).get("/api/users").status_code == 401


def test_list_forbidden_for_non_admin() -> None:
    c = _client_for(_mk_user(role="reviewer"))
    assert c.get("/api/users").status_code == 403


def test_admin_can_list_users() -> None:
    admin = _mk_user(role="admin")
    c = _client_for(admin)
    r = c.get("/api/users")
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert admin.email in emails


def test_create_requires_admin() -> None:
    c = _client_for(_mk_user(role="sales"))
    r = c.post(
        "/api/users",
        json={"email": "new@acme-travel.example", "full_name": "New", "role": "reviewer"},
    )
    assert r.status_code == 403


def test_admin_creates_user_lowercased() -> None:
    c = _client_for(_mk_user(role="admin"))
    email = f"Mixed-{uuid.uuid4()}@acme-travel.com"
    r = c.post(
        "/api/users",
        json={"email": email, "full_name": "Mixed Case", "role": "bdr"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == email.lower()


def test_deactivate_user_revokes_sessions() -> None:
    admin = _mk_user(role="admin")
    target = _mk_user(role="reviewer")
    target_client = _client_for(target)
    assert target_client.get("/api/auth/me").status_code == 200

    admin_client = _client_for(admin)
    r = admin_client.patch(f"/api/users/{target.id}", json={"active": False})
    assert r.status_code == 200
    assert r.json()["active"] is False
    # Target's live session is now revoked.
    assert target_client.get("/api/auth/me").status_code == 401


def test_cannot_deactivate_self() -> None:
    admin = _mk_user(role="admin")
    c = _client_for(admin)
    r = c.patch(f"/api/users/{admin.id}", json={"active": False})
    assert r.status_code == 400


def test_admin_can_change_another_users_role() -> None:
    admin = _mk_user(role="admin")
    target = _mk_user(role="reviewer")
    c = _client_for(admin)
    r = c.patch(f"/api/users/{target.id}", json={"role": "sales"})
    assert r.status_code == 200
    assert r.json()["role"] == "sales"


def test_cannot_demote_self() -> None:
    admin = _mk_user(role="admin")
    c = _client_for(admin)
    r = c.patch(f"/api/users/{admin.id}", json={"role": "reviewer"})
    assert r.status_code == 400
