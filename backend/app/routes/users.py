"""Users — minimal CRUD for the pilot. Real auth lands in v1.1 (spec §3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

router = APIRouter(prefix="/api/users", tags=["users"])


Role = Literal["admin", "reviewer", "sales", "bdr"]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1)
    role: Role


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db)) -> UserOut:
    user = User(email=body.email, full_name=body.full_name, role=body.role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"user with email {body.email!r} already exists",
        )
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, db: Session = Depends(get_db)) -> UserOut:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return UserOut.model_validate(user)
