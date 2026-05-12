"""Suppressions — manual block-list management (spec §12.1, §15).

Morgan/Jacob can add suppressions without touching SQL. Suppressions block at
the dedupe layer AND at the export layer (belt-and-braces).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Suppression, User
from app.routes.auth import current_user

router = APIRouter(prefix="/api/suppressions", tags=["suppressions"])


SuppressionType = Literal["email", "domain"]


class SuppressionCreate(BaseModel):
    type: SuppressionType
    value: str = Field(..., min_length=1, max_length=320)
    reason: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def _check_value_shape(self) -> "SuppressionCreate":
        v = self.value.strip()
        if self.type == "email" and "@" not in v:
            raise ValueError("email suppression value must contain '@'")
        if self.type == "domain" and "@" in v:
            raise ValueError("domain suppression value must not contain '@'")
        return self


class SuppressionOut(BaseModel):
    id: uuid.UUID
    type: str
    value: str
    reason: str
    added_by: uuid.UUID
    added_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def create_suppression(
    body: SuppressionCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SuppressionOut:
    sup = Suppression(
        type=body.type,
        value=body.value.strip(),
        value_lower=body.value.strip().lower(),
        reason=body.reason,
        added_by=user.id,
    )
    db.add(sup)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.type} suppression for {body.value!r} already exists",
        )
    db.refresh(sup)
    return SuppressionOut.model_validate(sup)


@router.get("", response_model=list[SuppressionOut])
def list_suppressions(
    type: SuppressionType | None = None,
    db: Session = Depends(get_db),
) -> list[SuppressionOut]:
    q = db.query(Suppression).order_by(Suppression.added_at.desc())
    if type is not None:
        q = q.filter(Suppression.type == type)
    return [SuppressionOut.model_validate(s) for s in q.all()]


@router.delete("/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_suppression(
    suppression_id: uuid.UUID,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    sup = db.query(Suppression).filter(Suppression.id == suppression_id).first()
    if sup is None:
        raise HTTPException(status_code=404, detail="suppression not found")
    db.delete(sup)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
