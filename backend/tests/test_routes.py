"""End-to-end route tests via FastAPI TestClient.

Covers the happy path through every router. Auth is the X-User-Id header
stub — real auth lands in v1.1. Pollution between tests is acceptable because
the routes don't depend on row counts; each test creates its own user/source/run.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------- helpers ----------


def _make_user(role: str = "admin") -> dict:
    r = client.post(
        "/api/users",
        json={
            "email": f"route-{uuid.uuid4()}@acme-travel.example",
            "full_name": "Route Test",
            "role": role,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_source(user_id: str) -> dict:
    r = client.post(
        "/api/sources",
        headers={"X-User-Id": user_id},
        json={
            "name": "Test Source",
            "url": "https://example.com/dir",
            "crawl_tier": 1,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_run(user_id: str, source_id: str, max_cost_cents: int = 200) -> dict:
    r = client.post(
        "/api/runs",
        headers={"X-User-Id": user_id},
        json={"source_id": source_id, "max_cost_cents": max_cost_cents},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------- users ----------


def test_create_user_and_fetch() -> None:
    created = _make_user()
    r = client.get(f"/api/users/{created['id']}")
    assert r.status_code == 200
    assert r.json()["email"] == created["email"]


def test_duplicate_email_conflict() -> None:
    u = _make_user()
    r = client.post(
        "/api/users",
        json={"email": u["email"], "full_name": "Dup", "role": "reviewer"},
    )
    assert r.status_code == 409


# ---------- auth header dependency ----------


def test_missing_user_header_400() -> None:
    r = client.post("/api/sources", json={"name": "n", "url": "https://x.com/"})
    assert r.status_code == 422  # FastAPI's missing-header validation


def test_bad_user_header_400() -> None:
    r = client.post(
        "/api/sources",
        headers={"X-User-Id": "not-a-uuid"},
        json={"name": "n", "url": "https://x.com/"},
    )
    assert r.status_code == 400


def test_unknown_user_header_401() -> None:
    r = client.post(
        "/api/sources",
        headers={"X-User-Id": str(uuid.uuid4())},
        json={"name": "n", "url": "https://x.com/"},
    )
    assert r.status_code == 401


# ---------- sources ----------


def test_create_and_list_sources() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    r = client.get("/api/sources")
    assert r.status_code == 200
    assert any(item["id"] == s["id"] for item in r.json())


def test_get_source_404() -> None:
    r = client.get(f"/api/sources/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------- runs ----------


def test_create_run_records_budget_approval() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    r = _make_run(u["id"], s["id"], max_cost_cents=150)
    assert r["max_cost_cents"] == 150
    assert r["budget_approved_by"] == u["id"]
    assert r["budget_approved_at"] is not None
    assert r["status"] == "pending"


def test_create_run_missing_source_404() -> None:
    u = _make_user()
    r = client.post(
        "/api/runs",
        headers={"X-User-Id": u["id"]},
        json={"source_id": str(uuid.uuid4()), "max_cost_cents": 100},
    )
    assert r.status_code == 404


def test_list_runs_filter_by_status() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    _make_run(u["id"], s["id"])
    r = client.get("/api/runs?status_filter=pending")
    assert r.status_code == 200
    body = r.json()
    assert all(item["status"] == "pending" for item in body)


# ---------- contacts ----------


def _seed_contact(user_id: str, source_id: str) -> tuple[str, str]:
    """Create a run + a contact directly via the ORM. Returns (run_id, contact_id).

    Uses a uuid-suffixed email so export_rows from prior test runs don't
    trip the "already exported in prior run" check.
    """
    from app.db import SessionLocal
    from app.models import Contact, ProspectRun
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        run = ProspectRun(
            source_id=uuid.UUID(source_id),
            created_by=uuid.UUID(user_id),
            max_cost_cents=500,
            budget_approved_by=uuid.UUID(user_id),
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        unique_email = f"test-{uuid.uuid4().hex[:8]}@example.org"
        c = Contact(
            run_id=run.id,
            name="Test Contact",
            email=unique_email,
            email_confidence="verified",
            evidence_excerpt="Test Contact teaches band at Example High School",
            evidence_verified="verified",
            review_status="pending",
            dedupe_status="new",
        )
        db.add(c)
        db.commit()
        return str(run.id), str(c.id)
    finally:
        db.close()


def test_list_contacts_filters_by_run() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    run_id, contact_id = _seed_contact(u["id"], s["id"])
    r = client.get(f"/api/contacts?run_id={run_id}")
    assert r.status_code == 200
    body = r.json()
    assert any(item["id"] == contact_id for item in body)


def test_review_contact_approve() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    _, contact_id = _seed_contact(u["id"], s["id"])
    r = client.post(
        f"/api/contacts/{contact_id}/review",
        headers={"X-User-Id": u["id"]},
        json={"action": "approve"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "approved"
    assert body["reviewed_by"] == u["id"]


def test_review_contact_edit_applies_payload() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    _, contact_id = _seed_contact(u["id"], s["id"])
    r = client.post(
        f"/api/contacts/{contact_id}/review",
        headers={"X-User-Id": u["id"]},
        json={
            "action": "edit",
            "payload": {"email": "corrected@example.org", "title": "Music Director"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "edited"
    assert body["email"] == "corrected@example.org"
    assert body["title"] == "Music Director"


def test_review_contact_reject() -> None:
    u = _make_user()
    s = _make_source(u["id"])
    _, contact_id = _seed_contact(u["id"], s["id"])
    r = client.post(
        f"/api/contacts/{contact_id}/review",
        headers={"X-User-Id": u["id"]},
        json={"action": "reject"},
    )
    assert r.status_code == 200
    assert r.json()["review_status"] == "rejected"


# ---------- exports ----------


def test_export_endpoint_writes_files(tmp_path, monkeypatch) -> None:
    """End-to-end: seed approved contact → POST export → file exists."""
    # Re-route the export root so tests don't write to /var/lib.
    from app.services import export_service as exp_mod

    orig = exp_mod.export_run

    def patched(*, db, run_id, created_by, export_root="/var/lib/prospectforge/exports"):
        return orig(db=db, run_id=run_id, created_by=created_by, export_root=str(tmp_path))

    monkeypatch.setattr(exp_mod, "export_run", patched)
    # The router imports export_run by name at module load — re-patch that too.
    from app.routes import exports as exports_router

    monkeypatch.setattr(exports_router, "export_run", patched)

    u = _make_user()
    s = _make_source(u["id"])
    run_id, contact_id = _seed_contact(u["id"], s["id"])

    # Approve so it lands in main.csv
    r = client.post(
        f"/api/contacts/{contact_id}/review",
        headers={"X-User-Id": u["id"]},
        json={"action": "approve"},
    )
    assert r.status_code == 200

    r = client.post(
        f"/api/runs/{run_id}/exports",
        headers={"X-User-Id": u["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["main"]["row_count"] == 1
    assert body["bdr_verify"]["row_count"] == 0


# ---------- suppressions ----------


def test_create_and_list_suppressions() -> None:
    u = _make_user()
    r = client.post(
        "/api/suppressions",
        headers={"X-User-Id": u["id"]},
        json={"type": "email", "value": "spam@bad.test", "reason": "complaints"},
    )
    assert r.status_code == 201, r.text
    sup_id = r.json()["id"]

    r = client.get("/api/suppressions?type=email")
    assert r.status_code == 200
    assert any(item["id"] == sup_id for item in r.json())

    r = client.delete(
        f"/api/suppressions/{sup_id}", headers={"X-User-Id": u["id"]}
    )
    assert r.status_code == 204


def test_suppression_validation() -> None:
    u = _make_user()
    # email type must contain @
    r = client.post(
        "/api/suppressions",
        headers={"X-User-Id": u["id"]},
        json={"type": "email", "value": "missing-at-sign", "reason": "x"},
    )
    assert r.status_code == 422


def test_suppression_duplicate_returns_409() -> None:
    u = _make_user()
    payload = {"type": "domain", "value": f"dup-{uuid.uuid4().hex}.test", "reason": "x"}
    r1 = client.post(
        "/api/suppressions", headers={"X-User-Id": u["id"]}, json=payload
    )
    assert r1.status_code == 201
    r2 = client.post(
        "/api/suppressions", headers={"X-User-Id": u["id"]}, json=payload
    )
    assert r2.status_code == 409
