"""Runs — Morgan kicks off a crawl/extract/dedupe pass against a Source.

Per spec §12.1, run creation requires a budget approval — `max_cost_cents`
is mandatory and the run records the user who approved it. The actual
crawl/extract worker scheduling lands when we wire RQ in a follow-up; for
now this endpoint creates the run row in `pending` status and an operator
or scheduler picks it up.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ProspectRun, Source, User
from app.routes._common import acting_user

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
    user: User = Depends(acting_user),
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
