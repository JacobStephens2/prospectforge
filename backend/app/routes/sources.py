"""Sources — Morgan registers a URL she wants to crawl."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Source, User
from app.routes._common import acting_user

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: HttpUrl
    segment_template: str | None = Field(None, max_length=200)
    crawl_tier: int = Field(1, ge=1, le=2)


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    segment_template: str | None
    crawl_tier: int
    active: bool
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    body: SourceCreate,
    user: User = Depends(acting_user),
    db: Session = Depends(get_db),
) -> SourceOut:
    source = Source(
        name=body.name,
        url=str(body.url),
        segment_template=body.segment_template,
        crawl_tier=body.crawl_tier,
        created_by=user.id,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return SourceOut.model_validate(source)


@router.get("", response_model=list[SourceOut])
def list_sources(
    active_only: bool = True,
    db: Session = Depends(get_db),
) -> list[SourceOut]:
    q = db.query(Source).order_by(Source.created_at.desc())
    if active_only:
        q = q.filter(Source.active.is_(True))
    return [SourceOut.model_validate(s) for s in q.all()]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> SourceOut:
    source = db.query(Source).filter(Source.id == source_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return SourceOut.model_validate(source)
