"""Worker entry point + CLI for the CRM Bridge nightly sync.

Run from inside the backend container:

    python -m app.workers.crm_sync

Or queue it as an RQ job by importing `run_full_sync` and enqueuing.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from app.db import SessionLocal
from app.services.crm_bridge import connect_crm, sync_agencies, sync_agent_users

log = logging.getLogger(__name__)


def run_full_sync() -> dict[str, Any]:
    """One-shot sync of both caches. Returns counts."""
    db = SessionLocal()
    try:
        with connect_crm() as conn:
            agencies = sync_agencies(db, crm_conn=conn)
            agent_users = sync_agent_users(db, crm_conn=conn)
    finally:
        db.close()
    return {"agencies_upserted": agencies, "agent_users_upserted": agent_users}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_full_sync()
    log.info("crm sync complete: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
