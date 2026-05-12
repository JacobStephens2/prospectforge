"""RQ entry point for executing a run. Thin wrapper around the orchestrator
so the same call works inline (the /start route uses this synchronously
today) and through `Queue.enqueue(run_runner.run, run_id)` once Redis is wired.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db import SessionLocal
from app.services.run_orchestrator import execute_run

log = logging.getLogger(__name__)


def run(run_id: str) -> dict[str, Any]:
    """RQ-callable entrypoint. Accepts a UUID string (RQ serializes args)."""
    rid = uuid.UUID(run_id)
    db = SessionLocal()
    try:
        summary = execute_run(db=db, run_id=rid)
        return {
            "run_id": str(rid),
            "final_status": summary.final_status,
            "pages_fetched": summary.pages_fetched,
            "pages_blocked": summary.pages_blocked,
            "orgs": summary.organizations_extracted,
            "contacts": summary.contacts_extracted,
            "cost_cents": summary.cost_cents,
            "error": summary.error,
        }
    finally:
        db.close()
