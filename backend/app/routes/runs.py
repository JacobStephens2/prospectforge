"""Runs — Morgan kicks off a crawl/extract/dedupe pass against a Source.

Per spec §12.1, run creation requires a budget approval — `max_cost_cents`
is mandatory and the run records the user who approved it. The actual
crawl/extract worker scheduling lands when we wire RQ in a follow-up; for
now this endpoint creates the run row in `pending` status and an operator
or scheduler picks it up.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import ProspectRun, Source, SourcePage, User
from app.routes.auth import current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunCreate(BaseModel):
    source_id: uuid.UUID
    max_cost_cents: int = Field(..., ge=1, le=100_000)
    estimated_cost_cents: int | None = Field(None, ge=0)


class RunOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    max_cost_cents: int
    estimated_cost_cents: int | None
    cost_cents: int
    tokens_used: int
    budget_approved_by: uuid.UUID | None
    budget_approved_at: datetime | None
    pages_crawled: int
    contacts_extracted: int
    created_by: uuid.UUID
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def create_run(
    body: RunCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RunOut:
    source = db.query(Source).filter(Source.id == body.source_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    if not source.active:
        raise HTTPException(status_code=400, detail="source is inactive")

    run = ProspectRun(
        source_id=source.id,
        status="pending",
        max_cost_cents=body.max_cost_cents,
        estimated_cost_cents=body.estimated_cost_cents,
        budget_approved_by=user.id,
        budget_approved_at=datetime.now(timezone.utc),
        created_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return RunOut.model_validate(run)


@router.get("", response_model=list[RunOut])
def list_runs(
    source_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RunOut]:
    q = db.query(ProspectRun).order_by(ProspectRun.created_at.desc())
    if source_id is not None:
        q = q.filter(ProspectRun.source_id == source_id)
    if status_filter is not None:
        q = q.filter(ProspectRun.status == status_filter)
    return [RunOut.model_validate(r) for r in q.limit(limit).all()]


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RunOut:
    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut.model_validate(run)


class RunStartResult(BaseModel):
    """Acknowledgement payload for the now-asynchronous start endpoint.

    The orchestrator runs in a background thread; the actual progress is
    visible via polling `/api/runs/{id}` and `/api/runs/{id}/pages`.
    """

    accepted: bool
    run_id: uuid.UUID
    status: str


def _run_in_background(run_id: uuid.UUID) -> None:
    """Thread target: open a fresh Session and drive the run. On any error
    we stamp the run as failed so the UI doesn't poll a stuck 'running' row
    forever. Threads are daemon=True so a container restart kills them
    cleanly; a re-runs queue is a follow-up (workers/run_runner.py is wired
    for RQ).
    """
    # Local import: defer importing the orchestrator until the thread runs,
    # to avoid pulling httpx + claude SDK into request-path import time.
    from app.services.run_orchestrator import execute_run

    db = SessionLocal()
    try:
        execute_run(db=db, run_id=run_id)
    except Exception:  # noqa: BLE001
        log.exception("background run %s crashed", run_id)
        try:
            db.rollback()
            run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
            if run is not None and run.status in ("pending", "running"):
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("failed to stamp run %s as failed", run_id)
    finally:
        db.close()


@router.post(
    "/{run_id}/start",
    response_model=RunStartResult,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    run_id: uuid.UUID,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RunStartResult:
    """Kick off the run in a background thread and return immediately.

    The thread takes the run from `pending` → `running` → `completed`
    (or `failed`/`over_budget`). Operators observe progress by polling
    `GET /api/runs/{id}` (status, pages_crawled, contacts_extracted)
    and `GET /api/runs/{id}/pages` (recent fetched URLs).

    202 Accepted is the right semantic: the work is queued, not done.
    """
    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"run is in status {run.status!r}, only 'pending' runs can be started",
        )

    thread = threading.Thread(
        target=_run_in_background,
        args=(run_id,),
        name=f"run-{run_id}",
        daemon=True,
    )
    thread.start()

    return RunStartResult(accepted=True, run_id=run_id, status="running")


class RunPageOut(BaseModel):
    """Lightweight projection of SourcePage for live progress views.

    Excludes content_markdown and raw_html_path (the heavy fields) so we
    can poll cheaply. URL + status + timing is all the UI needs.
    """

    id: uuid.UUID
    url: str
    canonical_url: str | None
    http_status: int | None
    status: str
    fetched_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/{run_id}/pages", response_model=list[RunPageOut])
def list_run_pages(
    run_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RunPageOut]:
    """Recent SourcePage rows for a run, newest first.

    Surfaces the live crawl trail: what was fetched, what was blocked
    (robots/SSRF), what 404'd. The Run detail page polls this every few
    seconds while status='running'.
    """
    run_exists = db.query(ProspectRun.id).filter(ProspectRun.id == run_id).first()
    if run_exists is None:
        raise HTTPException(status_code=404, detail="run not found")

    rows = (
        db.query(SourcePage)
        .filter(SourcePage.run_id == run_id)
        .order_by(SourcePage.created_at.desc())
        .limit(limit)
        .all()
    )
    return [RunPageOut.model_validate(r) for r in rows]
