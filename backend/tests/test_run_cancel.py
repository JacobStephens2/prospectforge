"""Cooperative run cancellation primitives in the orchestrator."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import ProspectRun, Source, User
from app.services.run_orchestrator import (
    _cancellation_requested,
    _set_run_status,
)


def _run(db, status: str) -> ProspectRun:
    u = User(email=f"cx-{uuid.uuid4()}@acme-travel.example", full_name="CX", role="admin")
    db.add(u)
    db.flush()
    s = Source(name="cx src", url="https://example.com/", created_by=u.id)
    db.add(s)
    db.flush()
    r = ProspectRun(
        source_id=s.id,
        created_by=u.id,
        max_cost_cents=100,
        budget_approved_by=u.id,
        budget_approved_at=datetime.now(timezone.utc),
        status=status,
    )
    db.add(r)
    db.commit()
    return r


def test_cancellation_requested_true_only_when_cancelling():
    db = SessionLocal()
    try:
        running = _run(db, "running")
        cancelling = _run(db, "cancelling")
        assert _cancellation_requested(db, running.id) is False
        assert _cancellation_requested(db, cancelling.id) is True
    finally:
        db.close()


def test_cancellation_requested_sees_external_commit():
    """A stop request lands on a different session; the orchestrator's fresh
    query must observe it (Postgres READ COMMITTED)."""
    db = SessionLocal()
    other = SessionLocal()
    try:
        run = _run(db, "running")
        assert _cancellation_requested(db, run.id) is False
        # Simulate the stop endpoint on a separate session/transaction.
        r2 = other.get(ProspectRun, run.id)
        r2.status = "cancelling"
        other.commit()
        assert _cancellation_requested(db, run.id) is True
    finally:
        db.close()
        other.close()


def test_set_run_status_cancelled_stamps_reason_and_completed_at():
    db = SessionLocal()
    try:
        run = _run(db, "cancelling")
        _set_run_status(db, run, "cancelled", failure_reason="Stopped by operator.")
        refreshed = db.get(ProspectRun, run.id)
        assert refreshed.status == "cancelled"
        assert refreshed.completed_at is not None
        assert refreshed.failure_reason == "Stopped by operator."
    finally:
        db.close()
