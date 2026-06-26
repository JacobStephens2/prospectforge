"""Users — the access whitelist. Every endpoint here is admin-only.

The `users` table IS the access whitelist: a magic link is only ever sent to
an address that has an `active=True` row (see services/auth.py). Managing who
can log in therefore means managing rows here — so these endpoints gate on the
caller having the `admin` role, not merely a valid session.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.routes.auth import current_user
from app.services.auth import revoke_all_sessions_for_user

router = APIRouter(prefix="/api/users", tags=["users"])


Role = Literal["admin", "reviewer", "sales", "bdr"]


def require_admin(user: User = Depends(current_user)) -> User:
    """Gate: caller must be an active admin. 403 otherwise."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin role required"
        )
    return user


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1)
    role: Role


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


def _active_admin_count(db: Session) -> int:
    return (
        db.query(func.count(User.id))
        .filter(User.role == "admin", User.active.is_(True))
        .scalar()
        or 0
    )


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[UserOut]:
    users = db.query(User).order_by(User.created_at).all()
    return [UserOut.model_validate(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> UserOut:
    user = User(
        email=str(body.email).strip().lower(),
        full_name=body.full_name,
        role=body.role,
    )
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
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> UserOut:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> UserOut:
    """Change a user's role and/or active flag.

    Guards against locking everyone out: you can't deactivate or demote
    yourself, and you can't remove the last remaining active admin.
    Deactivating a user also revokes their live sessions immediately.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    deactivating = body.active is False and user.active
    demoting = body.role is not None and body.role != "admin" and user.role == "admin"

    if user.id == admin.id and (deactivating or demoting):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="you can't deactivate or demote your own admin account",
        )

    # Removing admin status from the last active admin would orphan the system.
    if (deactivating or demoting) and user.role == "admin" and user.active:
        if _active_admin_count(db) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="can't remove the last active admin",
            )

    if body.role is not None:
        user.role = body.role
    if body.active is not None:
        user.active = body.active

    db.commit()
    db.refresh(user)

    if deactivating:
        revoke_all_sessions_for_user(db, user.id)

    return UserOut.model_validate(user)
