"""Promote an enrichment-suggested contact into the main review queue."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Contact, ProspectRun, ReviewAction, Source, User
from app.routes.auth import current_user


def _u(db) -> User:
    u = User(
        email=f"prom-{uuid.uuid4()}@acme-travel.example",
        full_name="P",
        role="admin",
    )
    db.add(u)
    db.flush()
    return u


def _s(db, user) -> Source:
    s = Source(name="prom src", url="https://example.com/", created_by=user.id)
    db.add(s)
    db.flush()
    return s


def _run(db, src, user) -> ProspectRun:
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


def _c(db, run, *, name="X", parent_id=None, review_status="pending") -> Contact:
    c = Contact(
        run_id=run.id,
        name=name,
        evidence_excerpt="x" * 30,
        parent_contact_id=parent_id,
        review_status=review_status,
    )
    db.add(c)
    db.flush()
    return c


def _with_auth(test_user_id, fn):
    def fake_user():
        d = SessionLocal()
        try:
            return d.get(User, test_user_id)
        finally:
            d.close()

    app.dependency_overrides[current_user] = fake_user
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_promote_clears_parent_contact_id():
    db = SessionLocal()
    try:
        u = _u(db)
        s = _s(db, u)
        r = _run(db, s, u)
        parent = _c(db, r, name="Parent")
        child = _c(db, r, name="Child", parent_id=parent.id)
        db.commit()
        cid = child.id
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post(f"/api/contacts/{cid}/promote")

    resp = _with_auth(uid, go)
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        refreshed = db.get(Contact, cid)
        assert refreshed.parent_contact_id is None
        # Audit row written.
        audits = (
            db.query(ReviewAction).filter(ReviewAction.contact_id == cid).all()
        )
        assert any(a.action == "promote" for a in audits)
    finally:
        db.close()


def test_promote_is_idempotent_on_standalone():
    """Calling promote on a contact that's already standalone does not
    raise; it returns the contact unchanged and writes no audit row."""
    db = SessionLocal()
    try:
        u = _u(db)
        s = _s(db, u)
        r = _run(db, s, u)
        already = _c(db, r, name="Already standalone")  # no parent
        db.commit()
        cid = already.id
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post(f"/api/contacts/{cid}/promote")

    resp = _with_auth(uid, go)
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        audits = (
            db.query(ReviewAction).filter(ReviewAction.contact_id == cid).all()
        )
        assert not any(a.action == "promote" for a in audits)
    finally:
        db.close()


def test_promote_404_on_unknown_contact():
    db = SessionLocal()
    try:
        u = _u(db)
        db.commit()
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post(f"/api/contacts/{uuid.uuid4()}/promote")

    resp = _with_auth(uid, go)
    assert resp.status_code == 404


def test_promote_resets_needs_review_to_pending():
    """A child contact whose evidence verification failed sits in
    needs_review. On promote, we move it back to pending so it joins the
    standard queue."""
    db = SessionLocal()
    try:
        u = _u(db)
        s = _s(db, u)
        r = _run(db, s, u)
        parent = _c(db, r, name="Parent")
        child = _c(
            db, r, name="Needs", parent_id=parent.id, review_status="needs_review"
        )
        db.commit()
        cid = child.id
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post(f"/api/contacts/{cid}/promote")

    resp = _with_auth(uid, go)
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        refreshed = db.get(Contact, cid)
        assert refreshed.review_status == "pending"
    finally:
        db.close()


def test_promote_preserves_approved_or_rejected_status():
    """A contact already approved/rejected keeps its review_status on
    promote — promoting is about queue visibility, not undoing decisions."""
    db = SessionLocal()
    try:
        u = _u(db)
        s = _s(db, u)
        r = _run(db, s, u)
        parent = _c(db, r, name="Parent")
        approved_child = _c(
            db, r, name="Approved", parent_id=parent.id, review_status="approved"
        )
        db.commit()
        cid = approved_child.id
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post(f"/api/contacts/{cid}/promote")

    _with_auth(uid, go)
    db = SessionLocal()
    try:
        refreshed = db.get(Contact, cid)
        assert refreshed.parent_contact_id is None
        assert refreshed.review_status == "approved"
    finally:
        db.close()


def test_bulk_promote_mixes_promoted_and_already_standalone():
    db = SessionLocal()
    try:
        u = _u(db)
        s = _s(db, u)
        r = _run(db, s, u)
        parent = _c(db, r, name="Parent")
        a = _c(db, r, name="A", parent_id=parent.id)
        b = _c(db, r, name="B")  # no parent — already standalone
        c = _c(db, r, name="C", parent_id=parent.id)
        db.commit()
        ids = [str(a.id), str(b.id), str(c.id), str(uuid.uuid4())]
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post(
            "/api/contacts/promote/bulk", json={"contact_ids": ids}
        )

    resp = _with_auth(uid, go)
    assert resp.status_code == 200
    body = resp.json()
    assert body["requested"] == 4
    assert body["promoted"] == 2
    assert body["already_standalone"] == 1
    assert len(body["not_found"]) == 1


def test_bulk_promote_rejects_empty():
    db = SessionLocal()
    try:
        u = _u(db)
        db.commit()
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        return client.post("/api/contacts/promote/bulk", json={"contact_ids": []})

    resp = _with_auth(uid, go)
    assert resp.status_code == 422


def test_bulk_promote_caps_at_500():
    db = SessionLocal()
    try:
        u = _u(db)
        db.commit()
        uid = u.id
    finally:
        db.close()

    def go():
        client = TestClient(app)
        ids = [str(uuid.uuid4()) for _ in range(501)]
        return client.post(
            "/api/contacts/promote/bulk", json={"contact_ids": ids}
        )

    resp = _with_auth(uid, go)
    assert resp.status_code == 422
