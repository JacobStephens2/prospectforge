"""Bulk enrichment endpoint — fire-and-forget runs for many contacts at once."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Contact, Organization, ProspectRun, Source, User
from app.routes.auth import current_user


def _make_user(db) -> User:
    u = User(
        email=f"bulk-{uuid.uuid4()}@acme-travel.example",
        full_name="Bulk",
        role="admin",
    )
    db.add(u)
    db.flush()
    return u


def _make_source(db, user: User) -> Source:
    s = Source(
        name=f"bulk source {uuid.uuid4()}",
        url="https://example.com/",
        created_by=user.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_org(db, run: ProspectRun, *, with_website: bool, with_name: bool = True) -> Organization:
    org = Organization(
        run_id=run.id,
        name="Test Org" if with_name else "",
        website="https://example.com/" if with_website else None,
        domain="example.com" if with_website else None,
    )
    db.add(org)
    db.flush()
    return org


def _make_run(db, src: Source, user: User) -> ProspectRun:
    r = ProspectRun(
        source_id=src.id,
        created_by=user.id,
        max_cost_cents=100,
        budget_approved_by=user.id,
        budget_approved_at=datetime.now(timezone.utc),
    )
    db.add(r)
    db.flush()
    return r


def _make_contact(db, run: ProspectRun, *, org: Organization | None = None, name: str = "Jane Smith") -> Contact:
    c = Contact(
        run_id=run.id,
        organization_id=org.id if org else None,
        name=name,
        evidence_excerpt="x" * 30,
    )
    db.add(c)
    db.flush()
    return c


def test_bulk_enrich_starts_runs_for_enrichable_contacts():
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = _make_org(db, run, with_website=True)
        c1 = _make_contact(db, run, org=org, name="Person A")
        c2 = _make_contact(db, run, org=org, name="Person B")
        db.commit()
        c1_id, c2_id = c1.id, c2.id
    finally:
        db.close()

    # Don't actually fire the background orchestrator threads in this test.
    with patch("threading.Thread") as MockThread, \
         patch.object(app, "dependency_overrides", {**app.dependency_overrides}) as _:
        # Auth override so we don't need a real session.
        db_for_auth = SessionLocal()
        try:
            test_user = _make_user(db_for_auth)
            db_for_auth.commit()
            tu_id = test_user.id
        finally:
            db_for_auth.close()

        def fake_user():
            db2 = SessionLocal()
            try:
                return db2.get(User, tu_id)
            finally:
                db2.close()

        app.dependency_overrides[current_user] = fake_user
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/contacts/enrich/bulk",
                json={"contact_ids": [str(c1_id), str(c2_id)]},
            )
        finally:
            app.dependency_overrides.pop(current_user, None)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["requested"] == 2
    assert body["started"] == 2
    assert body["skipped"] == []
    # One bounded-pool dispatcher thread is kicked off for the whole batch
    # (replacing the old one-thread-per-contact fan-out that could spawn
    # hundreds of threads at once). It's handed both run ids to drive under
    # the concurrency cap.
    assert MockThread.call_count == 1
    dispatched_run_ids = MockThread.call_args.kwargs["args"][0]
    assert len(dispatched_run_ids) == 2


def test_bulk_enrich_skips_contacts_with_no_seedable_org():
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        # Org with no website AND no domain AND no name → unseedable even
        # with SERP free-search.
        org = _make_org(db, run, with_website=False, with_name=False)
        c = _make_contact(db, run, org=org, name="No-Org Person")
        db.commit()
        cid = c.id

        test_user = _make_user(db)
        db.commit()
        tu_id = test_user.id
    finally:
        db.close()

    def fake_user():
        db2 = SessionLocal()
        try:
            return db2.get(User, tu_id)
        finally:
            db2.close()

    with patch("threading.Thread") as MockThread:
        app.dependency_overrides[current_user] = fake_user
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/contacts/enrich/bulk",
                json={"contact_ids": [str(cid)]},
            )
        finally:
            app.dependency_overrides.pop(current_user, None)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["requested"] == 1
    assert body["started"] == 0
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["contact_id"] == str(cid)
    assert "no seed URL" in body["skipped"][0]["reason"]
    # No background thread because no runs were created.
    assert MockThread.call_count == 0


def test_bulk_enrich_rejects_over_cap():
    """Hard cap of 500 — pydantic 422s on oversized payload."""
    db = SessionLocal()
    try:
        test_user = _make_user(db)
        db.commit()
        tu_id = test_user.id
    finally:
        db.close()

    def fake_user():
        db2 = SessionLocal()
        try:
            return db2.get(User, tu_id)
        finally:
            db2.close()

    app.dependency_overrides[current_user] = fake_user
    try:
        client = TestClient(app)
        oversized = [str(uuid.uuid4()) for _ in range(501)]
        resp = client.post(
            "/api/contacts/enrich/bulk",
            json={"contact_ids": oversized},
        )
    finally:
        app.dependency_overrides.pop(current_user, None)

    assert resp.status_code == 422


def test_bulk_enrich_rejects_empty_list():
    db = SessionLocal()
    try:
        test_user = _make_user(db)
        db.commit()
        tu_id = test_user.id
    finally:
        db.close()

    def fake_user():
        db2 = SessionLocal()
        try:
            return db2.get(User, tu_id)
        finally:
            db2.close()

    app.dependency_overrides[current_user] = fake_user
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/contacts/enrich/bulk", json={"contact_ids": []}
        )
    finally:
        app.dependency_overrides.pop(current_user, None)

    assert resp.status_code == 422
