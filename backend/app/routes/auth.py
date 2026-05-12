"""Auth routes — request magic link, verify, logout, who-am-i.

Cookie name: `pf_session`. HttpOnly, Secure (when not in dev), SameSite=Lax.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.services.auth import (
    SessionInvalid,
    TokenInvalid,
    request_magic_link,
    resolve_session,
    revoke_session,
    verify_magic_link_and_create_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "pf_session"


# ---------- shared dependency ----------


def current_user(
    pf_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    db: Session = Depends(get_db),
) -> User:
    """Resolve the cookie to a User. 401 on missing/invalid/expired/revoked."""
    if pf_session is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    resolved = resolve_session(db, pf_session)
    if resolved is None:
        raise HTTPException(status_code=401, detail="session invalid")
    _, user = resolved
    return user


# ---------- request ----------


class MagicLinkRequest(BaseModel):
    email: EmailStr


@router.post("/request", status_code=204)
def post_request(
    body: MagicLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Send a magic link if the email is whitelisted. Always 204, regardless
    of whether the email exists — don't leak whitelist contents."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        request_magic_link(
            db,
            email=str(body.email),
            public_url=settings.app_public_url,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception:
        # Mail-send failures: log but don't tell the caller. They'll see
        # nothing arrive and retry, which is correct behavior.
        import logging
        logging.getLogger(__name__).exception("magic-link send failed for %s", body.email)
    return Response(status_code=204)


# ---------- verify ----------


class VerifyResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    session_expires_at: datetime


@router.post("/verify", response_model=VerifyResponse)
def post_verify(
    request: Request,
    response: Response,
    token: str,
    db: Session = Depends(get_db),
) -> VerifyResponse:
    """Consume a magic-link token. Sets the pf_session cookie."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        session, user = verify_magic_link_and_create_session(
            db, token=token, ip_address=ip, user_agent=ua,
        )
    except TokenInvalid as exc:
        raise HTTPException(status_code=410, detail=str(exc))

    secure = settings.app_env != "development"
    response.set_cookie(
        key=COOKIE_NAME,
        value=session.token,
        max_age=int((session.expires_at - session.created_at).total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return VerifyResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        session_expires_at=session.expires_at,
    )


# ---------- logout ----------


@router.post("/logout", status_code=204)
def post_logout(
    response: Response,
    pf_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    db: Session = Depends(get_db),
) -> Response:
    if pf_session:
        revoke_session(db, pf_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return Response(status_code=204)


# ---------- who am i ----------


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str


@router.get("/me", response_model=MeResponse)
def get_me(user: User = Depends(current_user)) -> MeResponse:
    return MeResponse(id=user.id, email=user.email, full_name=user.full_name, role=user.role)
