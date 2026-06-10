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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Contact, ExportRow, ProspectRun, ReviewAction, User
from app.routes.auth import current_user
from app.services.enrichment_run import (
    EnrichmentNotPossible,
    list_enrichment_results,
    list_enrichment_runs,
    start_enrichment_run,
)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


# ---------- schemas ----------


class ContactOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    organization_id: uuid.UUID | None
    source_page_id: uuid.UUID | None
    source_page_url: str | None = None  # joined from source_pages for the UI
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
    previously_exported: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


def _previously_exported_ids(db: Session, contacts: list[Contact]) -> set[uuid.UUID]:
    """One batched lookup per page-of-rows. Returns the set of contact.ids
    that already appear in export_rows (by contact_id OR normalized email).

    Per spec §12.2: the review UI surfaces a 'Previously exported' badge so
    a reviewer can see at a glance that approving will hit the duplicate
    gate in export_service. Lookup is O(unique emails+ids), not O(rows).
    """
    if not contacts:
        return set()
    ids = [c.id for c in contacts]
    emails = [c.email.lower() for c in contacts if c.email]

    by_id: set[uuid.UUID] = set()
    if ids:
        by_id.update(
            r[0] for r in db.execute(
                select(ExportRow.contact_id).where(ExportRow.contact_id.in_(ids))
            ).all()
        )

    by_email_contact_ids: set[uuid.UUID] = set()
    if emails:
        emailed_set = set(
            r[0] for r in db.execute(
                select(ExportRow.exported_email).where(ExportRow.exported_email.in_(emails))
            ).all()
        )
        for c in contacts:
            if c.email and c.email.lower() in emailed_set:
                by_email_contact_ids.add(c.id)

    return by_id | by_email_contact_ids


def _hydrate(db: Session, contacts: list[Contact]) -> list[ContactOut]:
    """Hydrate a batch of contacts with source_page_url and previously_exported."""
    from app.models import SourcePage

    sp_ids = {c.source_page_id for c in contacts if c.source_page_id}
    sp_by_id: dict[uuid.UUID, SourcePage] = {}
    if sp_ids:
        for sp in db.query(SourcePage).filter(SourcePage.id.in_(sp_ids)).all():
            sp_by_id[sp.id] = sp

    prior = _previously_exported_ids(db, contacts)

    out: list[ContactOut] = []
    for c in contacts:
        item = ContactOut.model_validate(c)
        if c.source_page_id:
            sp = sp_by_id.get(c.source_page_id)
            if sp is not None:
                item.source_page_url = sp.canonical_url or sp.url
        item.previously_exported = c.id in prior
        out.append(item)
    return out


def _hydrate_one(db: Session, contact: Contact) -> ContactOut:
    return _hydrate(db, [contact])[0]


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
    has_contact_info: bool | None = Query(
        None,
        description="True — require email OR phone (reachable). False — only contacts missing both.",
    ),
    enrichment: str | None = Query(
        None,
        description=(
            "Enrichment outcome filter. 'succeeded' — an enrichment produced "
            "at least one suggestion with an email for this contact. "
            "'attempted_no_result' — enrichment was attempted (a run targeted "
            "this contact) but no email suggestion was produced."
        ),
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: User = Depends(current_user),
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
    if has_contact_info is True:
        q = q.filter((Contact.email.isnot(None)) | (Contact.phone.isnot(None)))
    elif has_contact_info is False:
        q = q.filter(Contact.email.is_(None) & Contact.phone.is_(None))
    if enrichment in ("succeeded", "attempted_no_result"):
        # "Success" is defined by outcome, not run status: an over_budget
        # crawl that still pattern-inferred + validated an email counts as
        # succeeded (the run label routinely says over_budget while the
        # suggestion is the real win).
        from sqlalchemy.orm import aliased

        Suggestion = aliased(Contact)
        succeeded = (
            db.query(Suggestion.id)
            .filter(
                Suggestion.parent_contact_id == Contact.id,
                Suggestion.email.isnot(None),
            )
            .exists()
        )
        if enrichment == "succeeded":
            q = q.filter(succeeded)
        else:
            attempted = (
                db.query(ProspectRun.id)
                .filter(ProspectRun.enrichment_for_contact_id == Contact.id)
                .exists()
            )
            q = q.filter(attempted, ~succeeded)
    rows = q.offset(offset).limit(limit).all()
    return _hydrate(db, rows)


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(
    contact_id: uuid.UUID,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ContactOut:
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return _hydrate_one(db, contact)


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
    return _hydrate_one(db, contact)


# ---------- per-contact enrichment ----------


class EnrichmentSerpAttempt(BaseModel):
    """One SERP query the enrichment attempted, with the URLs returned.

    Surfaced in the UI so reviewers can see what was searched and which
    candidates Brave/SerpAPI returned — useful for triaging runs that
    found no contacts (was the query bad? did SERP return junk?)."""

    query: str
    provider: str
    urls: list[str]
    note: str = ""


class EnrichmentRunOut(BaseModel):
    """Minimal projection of a ProspectRun, scoped to the enrichment surface."""

    id: uuid.UUID
    status: str
    pages_crawled: int
    contacts_extracted: int
    cost_cents: int
    max_cost_cents: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_reason: str | None = None
    # Trace of how the seed was picked + every SERP query attempted.
    seed_source: str | None = None
    seed_url: str | None = None
    serp_attempts: list[EnrichmentSerpAttempt] = []
    # URLs actually fetched by the orchestrator (one per source_page row).
    crawled_pages: list["EnrichmentCrawledPage"] = []

    model_config = {"from_attributes": True}


class EnrichmentCrawledPage(BaseModel):
    """A single page the enrichment crawl fetched."""

    url: str
    status: str          # fetched | blocked_robots | blocked_ssrf | error | ...
    http_status: int | None = None
    error: str | None = None
    has_contact_form: bool = False
    contact_form_actions: list[str] = []


class EnrichmentSummary(BaseModel):
    """Full enrichment view for a contact: runs that targeted it + the
    contacts those runs produced (the suggestions to surface in UI)."""

    parent_contact_id: uuid.UUID
    runs: list[EnrichmentRunOut]
    suggested_contacts: list[ContactOut]


@router.post(
    "/{contact_id}/enrich",
    response_model=EnrichmentRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_enrich(
    contact_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> EnrichmentRunOut:
    """Kick off a per-contact enrichment run. Returns the run row immediately
    (status='pending'); the orchestrator runs in a background thread, same
    pattern as POST /api/runs/{id}/start.

    Fails 400 if the contact's organization has no domain or website — the
    crawler needs a seed URL.
    """
    try:
        run = start_enrichment_run(
            db,
            contact_id=contact_id,
            requested_by_user_id=user.id,
        )
    except EnrichmentNotPossible as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Fire-and-forget: crawl + inference/vendor/validation follow-up.
    import threading

    from app.services.enrichment_followup import run_enrichment_in_background

    threading.Thread(
        target=run_enrichment_in_background, args=(run.id,), daemon=True
    ).start()

    return EnrichmentRunOut.model_validate(run)


class BulkEnrichBody(BaseModel):
    """Bulk enrichment request — start an enrichment run for each contact_id.

    Hard cap below MAX_BULK_ENRICH_IDS so a misclick on a 500-contact view
    can't kick off thousands of runs. If you want more, send multiple
    batches and confirm budget between each.
    """

    contact_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


class BulkEnrichSkip(BaseModel):
    contact_id: uuid.UUID
    reason: str


class BulkEnrichResult(BaseModel):
    requested: int
    started: int
    skipped: list[BulkEnrichSkip]


@router.post(
    "/enrich/bulk",
    response_model=BulkEnrichResult,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_bulk_enrich(
    body: BulkEnrichBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> BulkEnrichResult:
    """Kick off enrichment runs for many contacts at once. Each run is
    fire-and-forget in its own background thread, same shape as the
    per-contact /enrich endpoint. Contacts whose org has no website AND
    no domain AND no SERP free-search hit get skipped with a per-row
    reason so the UI can show what failed.

    Bounded at MAX_BULK_ENRICH_IDS contacts per request — the cap is
    enforced both by Pydantic max_length and the explicit check below
    for defense in depth.
    """
    import threading

    from app.services.enrichment_followup import run_enrichment_in_background

    skipped: list[BulkEnrichSkip] = []
    run_ids_to_start: list[uuid.UUID] = []
    for cid in body.contact_ids:
        try:
            run = start_enrichment_run(
                db,
                contact_id=cid,
                requested_by_user_id=user.id,
            )
            run_ids_to_start.append(run.id)
        except EnrichmentNotPossible as exc:
            skipped.append(BulkEnrichSkip(contact_id=cid, reason=str(exc)))

    # All runs are persisted in 'pending' before we fan out threads —
    # otherwise a request-aborting client could leave us with started
    # threads referencing run rows that aren't committed yet.
    for rid in run_ids_to_start:
        threading.Thread(
            target=run_enrichment_in_background, args=(rid,), daemon=True
        ).start()

    return BulkEnrichResult(
        requested=len(body.contact_ids),
        started=len(run_ids_to_start),
        skipped=skipped,
    )


class PromoteResult(BaseModel):
    """Outcome of promoting one contact from suggestion → standalone."""

    contact_id: uuid.UUID
    promoted: bool
    reason: str = ""


class BulkPromoteBody(BaseModel):
    contact_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


class BulkPromoteResult(BaseModel):
    requested: int
    promoted: int
    already_standalone: int
    not_found: list[uuid.UUID]


def _promote_one(db: Session, contact_id: uuid.UUID, user: User) -> PromoteResult:
    """Internal helper for /promote and /promote/bulk. Idempotent — a
    contact that's already standalone (parent_contact_id IS NULL) returns
    promoted=False with reason='already standalone' so the caller can
    surface accurate counts."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None:
        return PromoteResult(
            contact_id=contact_id, promoted=False, reason="not found"
        )
    if contact.parent_contact_id is None:
        return PromoteResult(
            contact_id=contact_id, promoted=False, reason="already standalone"
        )
    prev_parent = contact.parent_contact_id
    contact.parent_contact_id = None
    # Reset review_status to pending if it was needs_review — promoting
    # implies the operator wants to actively review it (default action
    # on standalone rows). Don't touch approved/rejected — those are
    # explicit human decisions to preserve.
    if contact.review_status == "needs_review":
        contact.review_status = "pending"
    db.add(
        ReviewAction(
            contact_id=contact.id,
            user_id=user.id,
            action="promote",
            payload={"previous_parent_contact_id": str(prev_parent)},
        )
    )
    return PromoteResult(contact_id=contact_id, promoted=True)


@router.post(
    "/{contact_id}/promote",
    response_model=ContactOut,
    status_code=status.HTTP_200_OK,
)
def post_promote(
    contact_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ContactOut:
    """Promote an enrichment-suggested contact into the main review queue.

    Clears parent_contact_id so the row stops appearing under its parent's
    suggestions and starts behaving like any other reviewable contact.
    Writes a 'promote' review_actions audit row. Idempotent on already-
    standalone rows (no-op).
    """
    result = _promote_one(db, contact_id, user)
    if result.reason == "not found":
        raise HTTPException(status_code=404, detail="contact not found")
    db.commit()
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    return _hydrate_one(db, contact)


@router.post(
    "/promote/bulk",
    response_model=BulkPromoteResult,
    status_code=status.HTTP_200_OK,
)
def post_bulk_promote(
    body: BulkPromoteBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> BulkPromoteResult:
    """Promote many enrichment-suggested contacts at once. Same semantics
    as the single endpoint, batched. Returns counts of what was actually
    moved vs already-standalone vs not-found."""
    promoted = 0
    already = 0
    not_found: list[uuid.UUID] = []
    for cid in body.contact_ids:
        r = _promote_one(db, cid, user)
        if r.promoted:
            promoted += 1
        elif r.reason == "already standalone":
            already += 1
        else:
            not_found.append(cid)
    db.commit()
    return BulkPromoteResult(
        requested=len(body.contact_ids),
        promoted=promoted,
        already_standalone=already,
        not_found=not_found,
    )


@router.get(
    "/{contact_id}/enrichments", response_model=EnrichmentSummary
)
def get_enrichments(
    contact_id: uuid.UUID,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> EnrichmentSummary:
    """Return all enrichment runs that targeted this contact, the SERP
    trace + crawled-page list per run, and the contacts those runs
    produced as suggestions."""
    from app.models import SourcePage

    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")

    runs = list_enrichment_runs(db, parent_contact_id=contact_id)
    suggestions = list_enrichment_results(db, parent_contact_id=contact_id)

    # Batch-fetch source_pages for all runs at once rather than N queries.
    run_ids = [r.id for r in runs]
    pages_by_run: dict[uuid.UUID, list[SourcePage]] = {rid: [] for rid in run_ids}
    if run_ids:
        rows = (
            db.query(SourcePage)
            .filter(SourcePage.run_id.in_(run_ids))
            .order_by(SourcePage.created_at.asc())
            .all()
        )
        for p in rows:
            pages_by_run.setdefault(p.run_id, []).append(p)

    runs_out: list[EnrichmentRunOut] = []
    for r in runs:
        ctx = r.enrichment_context or {}
        attempts = [
            EnrichmentSerpAttempt(
                query=a.get("query", ""),
                provider=a.get("provider", ""),
                urls=list(a.get("urls") or []),
                note=a.get("note", ""),
            )
            for a in (ctx.get("serp_attempts") or [])
        ]
        crawled = [
            EnrichmentCrawledPage(
                url=p.url,
                status=p.status,
                http_status=p.http_status,
                error=(p.page_metadata or {}).get("error")
                if isinstance(p.page_metadata, dict)
                else None,
                has_contact_form=bool(
                    (p.page_metadata or {}).get("has_contact_form")
                    if isinstance(p.page_metadata, dict)
                    else False
                ),
                contact_form_actions=list(
                    (p.page_metadata or {}).get("contact_form_actions") or []
                )
                if isinstance(p.page_metadata, dict)
                else [],
            )
            for p in pages_by_run.get(r.id, [])
        ]
        run_out = EnrichmentRunOut.model_validate(r)
        run_out.seed_source = ctx.get("seed_source")
        run_out.seed_url = ctx.get("seed_url")
        run_out.serp_attempts = attempts
        run_out.crawled_pages = crawled
        runs_out.append(run_out)

    return EnrichmentSummary(
        parent_contact_id=contact_id,
        runs=runs_out,
        suggested_contacts=_hydrate(db, suggestions),
    )
