"""Orphaned-run recovery — startup sweep that stamps zombies failed."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import ProspectRun, Source, User
from app.services.startup_recovery import (
    ORPHAN_STALE_MINUTES,
    recover_orphaned_runs,
)


def _u(db) -> User:
    u = User(
        email=f"rec-{uuid.uuid4()}@acme-travel.example",
        full_name="Rec",
        role="admin",
    )
    db.add(u)
    db.flush()
    return u


def _s(db, user: User) -> Source:
    s = Source(name="rec source", url="https://example.com/", created_by=user.id)
    db.add(s)
    db.flush()
    return s


def _run(db, src: Source, user: User, **kw) -> ProspectRun:
    r = ProspectRun(
        source_id=src.id,
        created_by=user.id,
        max_cost_cents=100,
        budget_approved_by=user.id,
        budget_approved_at=datetime.now(timezone.utc),
        **kw,
    )
    db.add(r)
    db.flush()
    return r


def test_stale_running_run_gets_marked_failed():
    db = SessionLocal()
    try:
        user = _u(db)
        src = _s(db, user)
        stale_start = datetime.now(timezone.utc) - timedelta(
            minutes=ORPHAN_STALE_MINUTES + 5
        )
        run = _run(db, src, user, status="running", started_at=stale_start)
        run_id = run.id
        db.commit()

        touched = recover_orphaned_runs(db)
        assert touched >= 1

        refreshed = db.get(ProspectRun, run_id)
        assert refreshed.status == "failed"
        assert refreshed.completed_at is not None
    finally:
        db.close()


def test_stale_pending_run_with_no_start_also_recovered():
    db = SessionLocal()
    try:
        user = _u(db)
        src = _s(db, user)
        # No started_at; created_at is the activity stamp. Force created_at
        # backdate by inserting then updating.
        run = _run(db, src, user, status="pending")
        run_id = run.id
        old = datetime.now(timezone.utc) - timedelta(
            minutes=ORPHAN_STALE_MINUTES + 5
        )
        from sqlalchemy import update as _update
        db.execute(
            _update(ProspectRun)
            .where(ProspectRun.id == run_id)
            .values(created_at=old)
        )
        db.commit()

        recover_orphaned_runs(db)
        refreshed = db.get(ProspectRun, run_id)
        assert refreshed.status == "failed"
    finally:
        db.close()


def test_fresh_running_run_left_alone():
    """A run started <ORPHAN_STALE_MINUTES ago must not be touched —
    legitimate in-flight runs would get killed otherwise."""
    db = SessionLocal()
    try:
        user = _u(db)
        src = _s(db, user)
        fresh_start = datetime.now(timezone.utc) - timedelta(minutes=2)
        run = _run(db, src, user, status="running", started_at=fresh_start)
        run_id = run.id
        db.commit()

        recover_orphaned_runs(db)
        refreshed = db.get(ProspectRun, run_id)
        assert refreshed.status == "running"
        assert refreshed.completed_at is None
    finally:
        db.close()


def test_completed_run_left_alone():
    """Recovery only touches active-state rows."""
    db = SessionLocal()
    try:
        user = _u(db)
        src = _s(db, user)
        old = datetime.now(timezone.utc) - timedelta(days=1)
        run = _run(
            db,
            src,
            user,
            status="completed",
            started_at=old,
            completed_at=old + timedelta(minutes=5),
        )
        run_id = run.id
        db.commit()

        recover_orphaned_runs(db)
        refreshed = db.get(ProspectRun, run_id)
        assert refreshed.status == "completed"
    finally:
        db.close()


def test_idempotent():
    """Running twice in a row is safe — second call touches zero rows."""
    db = SessionLocal()
    try:
        user = _u(db)
        src = _s(db, user)
        stale_start = datetime.now(timezone.utc) - timedelta(
            minutes=ORPHAN_STALE_MINUTES + 5
        )
        _run(db, src, user, status="running", started_at=stale_start)
        db.commit()

        first = recover_orphaned_runs(db)
        second = recover_orphaned_runs(db)
        assert first >= 1
        assert second == 0
    finally:
        db.close()
