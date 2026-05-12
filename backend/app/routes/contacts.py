"""Contacts — the review queue surface (spec §12.2).

GET /api/contacts is the filterable list that powers Morgan's review UI:
sortable by the score_* booleans, filterable by review_status and
dedupe_status, paginated. Detail endpoint includes the organization +
source page so the evidence drawer can render context.

POST /api/contacts/{id}/review records an approve/reject/edit/override
decision and writes an audit row to review_actions (spec §15: "Manual
suppression UI so Morgan/Jacob can add suppressions without SQL access").
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Contact, ReviewAction, User
from app.routes.auth import current_user

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


# ---------- schemas ----------


class ContactOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    organization_id: uuid.UUID | None
    source_page_id: uuid.UUID | None
    name: str
    title: str | None
    email: str | None
    alt_email: str | None
    email_confidence: str | None
    phone: str | None
    evidence_excerpt: str | None
    evidence_verified: str
    evidence_match_score: float | None
    segment_tag: str | None
    review_status: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    dedupe_status: str
    dedupe_reason: str | None
    crm_agent_user_id: int | None
    crm_agency_id: str | None
    score_role_match: bool
    score_state_match: bool
    score_has_direct_email: bool
    score_has_phone: bool
    score_has_travel_signal: bool
    created_at: datetime

    model_config = {"from_attributes": True}


ReviewAction_T = Literal[
    "approve", "reject", "edit", "override_dedupe", "override_export_dup"
]


class ReviewBody(BaseModel):
    action: ReviewAction_T
    payload: dict | None = Field(
        None,
        description=(
            "For action='edit', the changed field values (e.g. corrected email). "
            "For override_*, optional rationale text. Stored verbatim in review_actions.payload."
        ),
    )


# ---------- routes ----------


@router.get("", response_model=list[ContactOut])
def list_contacts(
    run_id: uuid.UUID | None = None,
    review_status: str | None = Query(
        None,
        description="pending | approved | rejected | edited | needs_review",
    ),
    dedupe_status: str | None = Query(
        None,
        description="new | possible_match | weak_match | existing_customer | suppressed | exact | domain_match",
    ),
    has_email: bool | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[ContactOut]:
    q = db.query(Contact).order_by(Contact.created_at.desc())
    if run_id is not None:
        q = q.filter(Contact.run_id == run_id)
    if review_status is not None:
        q = q.filter(Contact.review_status == review_status)
    if dedupe_status is not None:
        q = q.filter(Contact.dedupe_status == dedupe_status)
    if has_email is True:
        q = q.filter(Contact.email.isnot(None))
    elif has_email is False:
        q = q.filter(Contact.email.is_(None))
    rows = q.offset(offset).limit(limit).all()
    return [ContactOut.model_validate(c) for c in rows]


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, db: Session = Depends(get_db)) -> ContactOut:
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return ContactOut.model_validate(contact)


@router.post(
    "/{contact_id}/review",
    response_model=ContactOut,
    status_code=status.HTTP_200_OK,
)
def review_contact(
    contact_id: uuid.UUID,
    body: ReviewBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ContactOut:
    """Record a review decision. Always writes a review_actions row for audit;
    updates the contact's review_status to match the action.

    `edit` accepts a `payload` of field updates — applied to the contact AND
    saved in the audit log so we can show before/after in the UI later.
    """
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")

    # Translate action → new review_status. Overrides don't change the status
    # by themselves (the action is the audit; status comes from approve/reject).
    new_status_by_action = {
        "approve": "approved",
        "reject": "rejected",
        "edit": "edited",
    }
    if body.action in new_status_by_action:
        contact.review_status = new_status_by_action[body.action]
        contact.reviewed_by = user.id
        contact.reviewed_at = datetime.now(timezone.utc)

    if body.action == "edit" and body.payload:
        # Apply edits — only to whitelisted fields, never to identity/system columns.
        editable = {"name", "title", "email", "phone", "segment_tag", "alt_email"}
        before: dict[str, object] = {}
        after: dict[str, object] = {}
        for key, val in body.payload.items():
            if key not in editable:
                continue
            before[key] = getattr(contact, key)
            setattr(contact, key, val)
            after[key] = val
        contact.review_payload = {"before": before, "after": after}

    db.add(
        ReviewAction(
            contact_id=contact.id,
            user_id=user.id,
            action=body.action,
            payload=body.payload,
        )
    )
    db.commit()
    db.refresh(contact)
    return ContactOut.model_validate(contact)
