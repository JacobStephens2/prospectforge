"""Startup-time recovery for orphaned runs.

The orchestrator runs in a Python background thread inside the backend
process. When the container restarts (rebuild, deploy, crash), in-flight
threads are terminated by SIGTERM and the orchestrator's try/except/finally
block doesn't reliably reach the "stamp run failed" step. The result is
ProspectRun rows stuck in status='running' (or 'pending') forever, with
no completed_at and no way to know they're zombies.

This module exposes `recover_orphaned_runs(db)` to be called once at
backend startup. It marks any run in ('running','pending') whose last
activity is older than ORPHAN_STALE_MINUTES as 'failed' with an
audit-friendly note in completed_at + a stamped log message. Pages and
contacts the orphan produced are kept — they're real data; only the
run-row state is corrected.

ORPHAN_STALE_MINUTES is intentionally generous so a backend restart
during a normal-length run does NOT wipe out an in-flight run that's
just slow to log progress.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.models import ProspectRun

log = logging.getLogger(__name__)


# A run with no activity for this long is treated as orphaned. The
# orchestrator commits pages_crawled after every fetched URL, so
# "started_at older than this AND nothing happened lately" is a
# reliable orphan signal. 10 minutes is comfortably longer than any
# single fetch (HTTP timeout is 60s; an extraction LLM call is <30s).
ORPHAN_STALE_MINUTES = 10


def recover_orphaned_runs(db: Session) -> int:
    """Mark stale running/pending runs as failed. Returns the count
    of rows touched.

    Idempotent — running this twice in a row touches zero rows the
    second time. Safe to call multiple times during a startup race.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ORPHAN_STALE_MINUTES)
    now = datetime.now(timezone.utc)

    # "Activity" for a run is started_at (running case) or created_at
    # (pending case where it never even started). Either being older
    # than cutoff with the row still claiming active status = orphan.
    orphan_reason = (
        "Orphaned by a backend restart. The run was still in flight when the "
        "backend process stopped (deploy, rebuild, or crash), which killed the "
        "orchestrator thread before it could finish. Recovered at startup and "
        "marked failed — there is no orchestrator traceback for this case. "
        "Any pages and contacts it produced before the restart were kept. "
        "Re-run the source to continue."
    )
    stmt = (
        update(ProspectRun)
        .where(
            ProspectRun.status.in_(("running", "pending")),
            ProspectRun.completed_at.is_(None),
            or_(
                ProspectRun.started_at.is_(None) & (ProspectRun.created_at < cutoff),
                ProspectRun.started_at < cutoff,
            ),
        )
        .values(status="failed", completed_at=now, failure_reason=orphan_reason)
        # 'fetch' so the session's identity map is refreshed from the DB
        # after the bulk update — the default 'evaluate' can't handle the
        # mixed is_(None)/comparison condition and would leave cached
        # ORM objects with stale values until expiration.
        .execution_options(synchronize_session="fetch")
    )
    result = db.execute(stmt)
    touched = result.rowcount or 0
    db.commit()
    # SessionLocal is configured with expire_on_commit=False, so cached
    # ORM objects keep pre-update values. Expire so any subsequent
    # db.get(ProspectRun, ...) on this session re-fetches from the DB.
    db.expire_all()
    if touched:
        log.warning(
            "recover_orphaned_runs: stamped %d run(s) as failed "
            "(running/pending with no activity for >%dmin)",
            touched,
            ORPHAN_STALE_MINUTES,
        )
    else:
        log.info("recover_orphaned_runs: no orphaned runs")
    return touched
