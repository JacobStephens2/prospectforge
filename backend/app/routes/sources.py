"""Sources — Morgan registers a URL she wants to crawl."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Source, User
from app.routes.auth import current_user

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: HttpUrl
    segment_template: str | None = Field(None, max_length=200)
    crawl_tier: int = Field(1, ge=1, le=2)
    follow_external_links: bool = False
    max_external_pages: int = Field(20, ge=0, le=1000)


class SourceUpdate(BaseModel):
    """All fields optional — only the supplied ones are applied."""

    name: str | None = Field(None, min_length=1, max_length=200)
    url: HttpUrl | None = None
    segment_template: str | None = Field(None, max_length=200)
    crawl_tier: int | None = Field(None, ge=1, le=2)
    follow_external_links: bool | None = None
    max_external_pages: int | None = Field(None, ge=0, le=1000)
    active: bool | None = None


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    segment_template: str | None
    crawl_tier: int
    follow_external_links: bool
    max_external_pages: int
    active: bool
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    body: SourceCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SourceOut:
    source = Source(
        name=body.name,
        url=str(body.url),
        segment_template=body.segment_template,
        crawl_tier=body.crawl_tier,
        follow_external_links=body.follow_external_links,
        max_external_pages=body.max_external_pages,
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


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SourceOut:
    """Partial-update a Source. Only fields present in the body are applied.

    URL edits are allowed but conceptually move the source to a new seed —
    the frontend should warn before submitting.
    """
    source = db.query(Source).filter(Source.id == source_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    payload = body.model_dump(exclude_unset=True)
    if "url" in payload and payload["url"] is not None:
        payload["url"] = str(payload["url"])
    for key, value in payload.items():
        setattr(source, key, value)
    db.commit()
    db.refresh(source)
    return SourceOut.model_validate(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: uuid.UUID,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    # Soft-delete: flip `active` off so the source disappears from the default
    # list view but historical runs that reference it still resolve.
    source = db.query(Source).filter(Source.id == source_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    if source.active:
        source.active = False
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
