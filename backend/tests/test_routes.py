"""End-to-end route tests via FastAPI TestClient.

Authentication is by cookie now (the X-User-Id header is gone), so each
test gets either:
  - `authed_client` — a TestClient with current_user overridden to a
    freshly-seeded User. Use this for routes that require auth.
  - `anon_client`   — un-overridden; current_user 401s.
"""

from __future__ import annotations

import uuid


# ---------- users (creation is still open for bootstrap) ----------


def test_create_user_and_fetch(anon_client) -> None:
    email = f"newuser-{uuid.uuid4()}@acme-travel.example"
    r = anon_client.post(
        "/api/users",
        json={"email": email, "full_name": "Newie", "role": "reviewer"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    r = anon_client.get(f"/api/users/{user_id}")
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_duplicate_email_conflict(anon_client, seeded_user) -> None:
    r = anon_client.post(
        "/api/users",
        json={"email": seeded_user.email, "full_name": "Dup", "role": "reviewer"},
    )
    assert r.status_code == 409


# ---------- cookie-based auth dependency ----------


def test_protected_route_requires_session(anon_client) -> None:
    r = anon_client.post(
        "/api/sources",
        json={"name": "n", "url": "https://x.com/"},
    )
    assert r.status_code == 401


# ---------- sources ----------


def test_create_and_list_sources(authed_client) -> None:
    r = authed_client.post(
        "/api/sources",
        json={"name": "Test Src", "url": "https://example.com/", "crawl_tier": 1},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = authed_client.get("/api/sources")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())


def test_get_source_404(anon_client) -> None:
    r = anon_client.get(f"/api/sources/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------- runs ----------


def test_create_run_records_budget_approval(authed_client, seeded_user) -> None:
    s = authed_client.post(
        "/api/sources",
        json={"name": "S", "url": "https://example.com/", "crawl_tier": 1},
    ).json()
    r = authed_client.post(
        "/api/runs", json={"source_id": s["id"], "max_cost_cents": 150}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["max_cost_cents"] == 150
    assert body["budget_approved_by"] == str(seeded_user.id)
    assert body["status"] == "pending"


def test_create_run_missing_source_404(authed_client) -> None:
    r = authed_client.post(
        "/api/runs",
        json={"source_id": str(uuid.uuid4()), "max_cost_cents": 100},
    )
    assert r.status_code == 404


# ---------- contacts ----------


def _seed_contact(user_id: uuid.UUID, source_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    from app.db import SessionLocal
    from app.models import Contact, ProspectRun
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        run = ProspectRun(
            source_id=source_id,
            created_by=user_id,
            max_cost_cents=500,
            budget_approved_by=user_id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        c = Contact(
            run_id=run.id,
            name="Test Contact",
            email=f"test-{uuid.uuid4().hex[:8]}@example.org",
            email_confidence="verified",
            evidence_excerpt="Test Contact teaches band at Example High School",
            evidence_verified="verified",
            review_status="pending",
            dedupe_status="new",
        )
        db.add(c)
        db.commit()
        return run.id, c.id
    finally:
        db.close()


def test_review_contact_approve(authed_client, seeded_user) -> None:
    s = authed_client.post(
        "/api/sources", json={"name": "S", "url": "https://example.com/", "crawl_tier": 1}
    ).json()
    _, contact_id = _seed_contact(seeded_user.id, uuid.UUID(s["id"]))
    r = authed_client.post(f"/api/contacts/{contact_id}/review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "approved"
    assert body["reviewed_by"] == str(seeded_user.id)


def test_review_contact_edit_applies_payload(authed_client, seeded_user) -> None:
    s = authed_client.post(
        "/api/sources", json={"name": "S", "url": "https://example.com/", "crawl_tier": 1}
    ).json()
    _, contact_id = _seed_contact(seeded_user.id, uuid.UUID(s["id"]))
    r = authed_client.post(
        f"/api/contacts/{contact_id}/review",
        json={"action": "edit", "payload": {"email": "corrected@example.org", "title": "Music Director"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["review_status"] == "edited"
    assert body["email"] == "corrected@example.org"
    assert body["title"] == "Music Director"


def test_review_contact_reject(authed_client, seeded_user) -> None:
    s = authed_client.post(
        "/api/sources", json={"name": "S", "url": "https://example.com/", "crawl_tier": 1}
    ).json()
    _, contact_id = _seed_contact(seeded_user.id, uuid.UUID(s["id"]))
    r = authed_client.post(f"/api/contacts/{contact_id}/review", json={"action": "reject"})
    assert r.status_code == 200
    assert r.json()["review_status"] == "rejected"


# ---------- exports ----------


def test_export_endpoint_writes_files(tmp_path, monkeypatch, authed_client, seeded_user) -> None:
    from app.services import export_service as exp_mod

    orig = exp_mod.export_run

    def patched(*, db, run_id, created_by, export_root="/var/lib/prospectforge/exports"):
        return orig(db=db, run_id=run_id, created_by=created_by, export_root=str(tmp_path))

    monkeypatch.setattr(exp_mod, "export_run", patched)
    from app.routes import exports as exports_router

    monkeypatch.setattr(exports_router, "export_run", patched)

    s = authed_client.post(
        "/api/sources", json={"name": "S", "url": "https://example.com/", "crawl_tier": 1}
    ).json()
    run_id, contact_id = _seed_contact(seeded_user.id, uuid.UUID(s["id"]))
    authed_client.post(f"/api/contacts/{contact_id}/review", json={"action": "approve"})

    r = authed_client.post(f"/api/runs/{run_id}/exports")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["main"]["row_count"] == 1
    assert body["bdr_verify"]["row_count"] == 0


# ---------- suppressions ----------


def test_create_and_list_suppressions(authed_client) -> None:
    payload = {"type": "email", "value": f"spam-{uuid.uuid4().hex[:8]}@bad.test", "reason": "complaints"}
    r = authed_client.post("/api/suppressions", json=payload)
    assert r.status_code == 201, r.text
    sup_id = r.json()["id"]
    r = authed_client.get("/api/suppressions?type=email")
    assert r.status_code == 200
    assert any(s["id"] == sup_id for s in r.json())
    r = authed_client.delete(f"/api/suppressions/{sup_id}")
    assert r.status_code == 204


def test_suppression_validation(authed_client) -> None:
    r = authed_client.post(
        "/api/suppressions",
        json={"type": "email", "value": "missing-at-sign", "reason": "x"},
    )
    assert r.status_code == 422


def test_suppression_duplicate_returns_409(authed_client) -> None:
    payload = {"type": "domain", "value": f"dup-{uuid.uuid4().hex}.test", "reason": "x"}
    r1 = authed_client.post("/api/suppressions", json=payload)
    assert r1.status_code == 201
    r2 = authed_client.post("/api/suppressions", json=payload)
    assert r2.status_code == 409
