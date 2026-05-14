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
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Contact, Organization, ProspectRun, Source, SourcePage, User
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
    source_name: str | None = None  # joined from sources for the runs list
    source_crawl_tier: int | None = None  # joined from sources for the runs list
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


def _runs_to_out(db: Session, runs: list[ProspectRun]) -> list[RunOut]:
    """Hydrate a list of runs with their Source name + crawl_tier. One
    batched lookup keyed by source_id — O(unique sources), not O(runs)."""
    source_ids = {r.source_id for r in runs}
    sources = (
        db.query(Source).filter(Source.id.in_(source_ids)).all() if source_ids else []
    )
    by_id = {s.id: s for s in sources}
    out: list[RunOut] = []
    for r in runs:
        item = RunOut.model_validate(r)
        s = by_id.get(r.source_id)
        if s is not None:
            item.source_name = s.name
            item.source_crawl_tier = s.crawl_tier
        out.append(item)
    return out


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
    return _runs_to_out(db, [run])[0]


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
    return _runs_to_out(db, q.limit(limit).all())


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: uuid.UUID,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a run and everything attached to it.

    Cascades take care of source_pages, organizations, contacts, run_jobs,
    review_actions (via contacts). Exports don't cascade from prospect_runs
    by FK, so we delete them explicitly first; their export_rows children
    cascade off the export row.

    Also clears the on-disk artifacts:
      /var/lib/prospectforge/exports/<run_id>/   (CSVs)
      /var/lib/prospectforge/archive/<run_id>/   (raw HTML)

    Refuses to delete a `running` run because the orchestrator thread still
    holds a Session against those rows — caller should wait for it to
    terminate (or restart the backend container).
    """
    import shutil
    from pathlib import Path
    from app.models import Export

    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status == "running":
        raise HTTPException(
            status_code=409,
            detail="run is currently running; wait for it to finish before deleting",
        )

    db.query(Export).filter(Export.run_id == run_id).delete(synchronize_session=False)
    db.delete(run)
    db.commit()

    # Best-effort filesystem cleanup. We swallow OSError so a stale
    # permission glitch can't block the database delete from succeeding.
    for root in ("/var/lib/prospectforge/exports", "/var/lib/prospectforge/archive"):
        path = Path(root) / str(run_id)
        if path.exists():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                log.warning("failed to clear %s: %s", path, exc)


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RunOut:
    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _runs_to_out(db, [run])[0]


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
    can poll cheaply. URL + status + timing + per-page contact/org counts
    is all the UI needs.
    """

    id: uuid.UUID
    url: str
    canonical_url: str | None
    http_status: int | None
    status: str
    fetched_at: datetime | None
    created_at: datetime
    contacts_extracted: int = 0
    organizations_extracted: int = 0

    model_config = {"from_attributes": True}


@router.get("/{run_id}/pages", response_model=list[RunPageOut])
def list_run_pages(
    run_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RunPageOut]:
    """Recent SourcePage rows for a run, newest first.

    Surfaces the live crawl trail: what was fetched, what was blocked
    (robots/SSRF), what 404'd, and how many contacts/orgs each fetched
    page produced. The Run detail page polls this every few seconds
    while status='running'.
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

    page_ids = [r.id for r in rows]
    contact_counts: dict[uuid.UUID, int] = {}
    org_counts: dict[uuid.UUID, int] = {}
    if page_ids:
        for pid, n in (
            db.query(Contact.source_page_id, func.count(Contact.id))
            .filter(Contact.source_page_id.in_(page_ids))
            .group_by(Contact.source_page_id)
            .all()
        ):
            contact_counts[pid] = n
        for pid, n in (
            db.query(Organization.source_page_id, func.count(Organization.id))
            .filter(Organization.source_page_id.in_(page_ids))
            .group_by(Organization.source_page_id)
            .all()
        ):
            org_counts[pid] = n

    out: list[RunPageOut] = []
    for r in rows:
        item = RunPageOut.model_validate(r)
        item.contacts_extracted = contact_counts.get(r.id, 0)
        item.organizations_extracted = org_counts.get(r.id, 0)
        out.append(item)
    return out
