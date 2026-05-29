"""Email deliverability validation (enrichment gate).

Inferred and vendor-sourced emails are guesses until a validator confirms
them. This module wraps NeverBounce's single-check endpoint. It's the gate
that lets the enrichment flow decide whether a candidate email is sendable.

NeverBounce result codes map to our ValidationStatus:
  valid       -> VALID        (deliverable; safe to send)
  invalid     -> INVALID      (will bounce; drop)
  catchall    -> CATCHALL     (domain accepts anything; can't confirm)
  disposable  -> INVALID      (throwaway mailbox; treat as unsendable)
  unknown     -> UNKNOWN      (NB couldn't determine)
  (no key)    -> UNKNOWN      (validation disabled)

UNKNOWN is the safe default everywhere — when we can't confirm, we don't
claim the email is good. The enrichment flow labels UNKNOWN/CATCHALL
candidates as not-sendable (email_confidence='pattern_guess' or
'unverifiable_catchall') so a reviewer makes the final call.
"""

from __future__ import annotations

import enum
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_NEVERBOUNCE_ENDPOINT = "https://api.neverbounce.com/v4/single/check"


class ValidationStatus(str, enum.Enum):
    VALID = "valid"
    INVALID = "invalid"
    CATCHALL = "catchall"
    UNKNOWN = "unknown"


def validate_email(email: str) -> ValidationStatus:
    """Check one email's deliverability via NeverBounce. Returns UNKNOWN
    when no key is configured, on any transport/API error, or when NB
    itself returns 'unknown' — callers must treat UNKNOWN as 'not
    confirmed', never as 'good'."""
    if not email or "@" not in email:
        return ValidationStatus.INVALID
    if not settings.neverbounce_api_key:
        return ValidationStatus.UNKNOWN

    params = {
        "key": settings.neverbounce_api_key,
        "email": email,
    }
    try:
        with httpx.Client(
            timeout=settings.enrichment_request_timeout_secs
        ) as cli:
            resp = cli.get(_NEVERBOUNCE_ENDPOINT, params=params)
    except httpx.HTTPError as exc:
        log.warning("neverbounce transport error for %s: %s", email, exc)
        return ValidationStatus.UNKNOWN
    if resp.status_code >= 400:
        log.warning(
            "neverbounce http %d for %s: %s",
            resp.status_code,
            email,
            resp.text[:200],
        )
        return ValidationStatus.UNKNOWN

    body = resp.json()
    # NeverBounce wraps errors in {status: 'auth_failure'|'general_failure'}
    if body.get("status") and body.get("status") != "success":
        log.warning("neverbounce api error for %s: %s", email, body.get("message"))
        return ValidationStatus.UNKNOWN

    result = (body.get("result") or "").lower()
    return {
        "valid": ValidationStatus.VALID,
        "invalid": ValidationStatus.INVALID,
        "disposable": ValidationStatus.INVALID,
        "catchall": ValidationStatus.CATCHALL,
        "unknown": ValidationStatus.UNKNOWN,
    }.get(result, ValidationStatus.UNKNOWN)


def confidence_label_for(status: ValidationStatus) -> str:
    """Translate a validation outcome into the Contact.email_confidence
    vocabulary used across the app + UI badges."""
    if status == ValidationStatus.VALID:
        return "verified"
    if status == ValidationStatus.CATCHALL:
        return "unverifiable_catchall"
    # INVALID never reaches here (caller drops it); UNKNOWN means "we have
    # a candidate but couldn't confirm" → pattern_guess so the UI shows
    # the amber "verify before sending" badge.
    return "pattern_guess"
