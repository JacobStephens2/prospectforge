"""Auth service — magic-link request + verify + session lifecycle.

Threat model + design choices:

  - **Only whitelisted emails get a link.** The whitelist IS the users table:
    rows with `active=true`. Anyone else asking for a link gets the same
    204 response with no email sent, so we don't leak which addresses exist.

  - **Tokens are 32 bytes from secrets.token_urlsafe.** 256 bits of entropy
    is more than enough; the bottleneck for online attacks is rate limiting.
    Magic links live 15 minutes; sessions live 30 days.

  - **Single-use magic links.** `used_at` flips on first /verify. A second
    /verify with the same token returns 410 Gone.

  - **Sessions are server-side.** Cookie carries only the random token; the
    `auth_sessions` row is what authorizes a request. Revocation just sets
    `revoked_at`. Logout works without trusting the client.

  - **Constant-time token comparison.** We use `secrets.compare_digest` for
    the equality check after looking up by token — defends against timing
    attacks on cache-hit-vs-miss differences. (The lookup-by-token already
    runs against an indexed column, so any timing leak there is much
    smaller than the network jitter floor.)
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthSession, MagicLinkToken, User

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Sentinel base. Subclasses get specific HTTP codes in the route layer."""


class TokenInvalid(AuthError):
    """Token doesn't exist, is malformed, expired, or already used."""


class SessionInvalid(AuthError):
    """Session cookie present but doesn't resolve to an active session."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- magic-link request ----------


def request_magic_link(
    db: Session,
    *,
    email: str,
    public_url: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """Create + email a magic link if `email` matches an active User.

    Returns True if a link was sent, False if the email isn't whitelisted.
    Callers MUST ignore the return value when responding to the user —
    leaking which addresses exist defeats the whitelist's purpose.
    """
    email_norm = email.strip().lower()

    user = (
        db.query(User)
        .filter(User.email == email_norm, User.active.is_(True))
        .first()
    )

    if user is None:
        log.info("magic-link denied (not whitelisted): %s", email_norm)
        return False

    token_value = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(minutes=settings.auth_magic_link_minutes)
    token_row = MagicLinkToken(
        token=token_value,
        email=email_norm,
        user_id=user.id,
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(token_row)
    db.commit()

    link = f"{public_url.rstrip('/')}/verify?token={token_value}"

    # Lazy import so tests can stub the mailer easily.
    from app.services.mailer import send_magic_link

    send_magic_link(
        to=email_norm, link=link, expires_minutes=settings.auth_magic_link_minutes
    )
    return True


# ---------- verify ----------


def verify_magic_link_and_create_session(
    db: Session,
    *,
    token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[AuthSession, User]:
    """Consume a magic-link token; create + return a new auth session."""

    if not token or len(token) < 16:
        raise TokenInvalid("token too short")

    row = db.query(MagicLinkToken).filter(MagicLinkToken.token == token).first()
    if row is None:
        raise TokenInvalid("unknown token")

    # Constant-time second check — paranoid since SQLAlchemy already
    # matched, but it's cheap and we'd regret skipping it later.
    if not secrets.compare_digest(row.token, token):
        raise TokenInvalid("token mismatch")

    if row.used_at is not None:
        raise TokenInvalid("token already used")
    if row.expires_at < _now():
        raise TokenInvalid("token expired")
    if row.user_id is None:
        # Shouldn't happen — we only create rows with a user_id — but defensive.
        raise TokenInvalid("token has no user")

    user = db.get(User, row.user_id)
    if user is None or not user.active:
        raise TokenInvalid("user gone or inactive")

    row.used_at = _now()

    session_token = secrets.token_urlsafe(32)
    session_row = AuthSession(
        user_id=user.id,
        token=session_token,
        expires_at=_now() + timedelta(days=settings.auth_session_days),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    return session_row, user


# ---------- session lookup ----------


def resolve_session(db: Session, token: str | None) -> tuple[AuthSession, User] | None:
    """Look up a session by its cookie token. None if missing/invalid/revoked."""
    if not token or len(token) < 16:
        return None
    row = db.query(AuthSession).filter(AuthSession.token == token).first()
    if row is None:
        return None
    if not secrets.compare_digest(row.token, token):
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at < _now():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None
    # Cheap last-seen refresh — bump only every 5 minutes so we don't write
    # on every request.
    if (_now() - row.last_seen_at).total_seconds() > 300:
        row.last_seen_at = _now()
        db.commit()
    return row, user


def revoke_session(db: Session, token: str) -> None:
    """Logout. Idempotent — re-revoking is a no-op."""
    row = db.query(AuthSession).filter(AuthSession.token == token).first()
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = _now()
    db.commit()


def revoke_all_sessions_for_user(db: Session, user_id: uuid.UUID) -> int:
    """Force-logout everywhere for a user. Useful for admin actions."""
    rows = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .all()
    )
    now = _now()
    for r in rows:
        r.revoked_at = now
    db.commit()
    return len(rows)
