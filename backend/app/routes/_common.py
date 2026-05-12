"""Cross-router helpers — auth stub, pagination, common dependencies.

Spec §3 has the real auth target ("static token behind office VPN for pilot;
internal SSO target v1.1"). For now, the X-User-Id header names the acting
user — the VPN is the actual security boundary. When SSO lands, swap this
dependency for the real identity middleware; route signatures don't change.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


def acting_user(
    x_user_id: str = Header(..., alias="X-User-Id", description="UUID of the acting user"),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the header to a User row. 401 if missing/invalid, 404 if unknown."""
    try:
        user_id = uuid.UUID(x_user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id must be a UUID",
        )
    user = db.query(User).filter(User.id == user_id, User.active.is_(True)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown or inactive user",
        )
    return user
