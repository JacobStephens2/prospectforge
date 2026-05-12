"""Extract worker — pulls one SourcePage, runs the LLM extraction, writes
Organization + Contact rows. RQ entry point: `extract_page(run_id, page_id)`.

Behavior:

- Reads the page (must be status='fetched' with non-empty content_markdown).
  Skips otherwise — pages that failed crawl have nothing to extract.
- Calls services/extraction.py for the LLM round-trip + evidence verification.
- Writes one Organization per extracted org, one Contact per kept contact.
- Sets `contacts.evidence_verified` from the verifier outcome; non-verified
  rows become review_status='needs_review' rather than being dropped, so
  Morgan can override if the evidence is real but the verifier was too strict.
- Updates prospect_runs.contacts_extracted as we go.
- Idempotency: contacts use the partial unique index on (run_id, lower(email))
  where email IS NOT NULL. A second extraction of the same email in the same
  run conflicts; we catch IntegrityError and skip the dupe.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Contact, Organization, ProspectRun, SourcePage
from app.services.budget import OverBudget
from app.services.extraction import (
    ExtractionRunResult,
    VerifiedContact,
    extract_from_page,
)

log = logging.getLogger(__name__)


def _persist(
    db,
    *,
    page: SourcePage,
    result: ExtractionRunResult,
) -> dict[str, int]:
    """Write organizations + verified contacts. Returns counts."""
    # Map of ExtractedOrganization.name → DB Organization.id so contacts can
    # link to the right one. For now, name is the natural key per page (a
    # later phase de-dupes orgs across pages within a run via trgm).
    org_id_by_name: dict[str, uuid.UUID] = {}
    orgs_written = 0
    for extracted_org in result.organizations:
        org = Organization(
            run_id=page.run_id,
            source_page_id=page.id,
            name=extracted_org.name,
            domain=extracted_org.domain,
            state=extracted_org.state,
            school_type=extracted_org.school_type,
        )
        db.add(org)
        db.flush()
        org_id_by_name[extracted_org.name] = org.id
        orgs_written += 1

    contacts_written = 0
    contacts_needs_review = 0
    contacts_skipped_dup = 0
    for vc in result.verified_contacts:
        review_status = "pending" if vc.keep else "needs_review"
        if not vc.keep:
            contacts_needs_review += 1
        c = Contact(
            run_id=page.run_id,
            organization_id=org_id_by_name.get(vc.organization.name),
            source_page_id=page.id,
            name=vc.contact.name,
            title=vc.contact.title,
            email=(vc.contact.email or None),
            phone=vc.contact.phone,
            email_confidence="verified" if vc.contact.email else None,
            evidence_excerpt=vc.contact.evidence_excerpt,
            evidence_verified=vc.evidence.status,
            evidence_match_score=vc.evidence.score,
            segment_tag=vc.contact.segment_tag,
            prompt_version_name=result.prompt_version[0],
            prompt_version_value=result.prompt_version[1],
            review_status=review_status,
            # score_* fields default to False; scoring worker fills them in.
        )
        db.add(c)
        try:
            db.flush()
            contacts_written += 1
        except IntegrityError:
            # Partial unique index hit — same (run_id, lower(email)) already
            # has a row in this run. Skip the dupe; the existing row wins.
            db.rollback()
            contacts_skipped_dup += 1

    # Bump the run's running tally atomically.
    if contacts_written:
        db.execute(
            update(ProspectRun)
            .where(ProspectRun.id == page.run_id)
            .values(contacts_extracted=ProspectRun.contacts_extracted + contacts_written)
        )
    db.commit()

    return {
        "orgs_written": orgs_written,
        "contacts_written": contacts_written,
        "contacts_needs_review": contacts_needs_review,
        "contacts_skipped_dup": contacts_skipped_dup,
    }


def extract_page(page_id: str) -> dict[str, Any]:
    """RQ entry point. Identifies its own run via the page row.

    `page_id` is a UUID string — the SourcePage primary key.
    """
    page_uuid = uuid.UUID(page_id)
    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_uuid).first()
        if page is None:
            return {"status": "skipped", "reason": f"page {page_id} not found"}
        if page.status != "fetched":
            return {"status": "skipped", "reason": f"page status={page.status!r}"}
        if not page.content_markdown:
            return {"status": "skipped", "reason": "page has no content_markdown"}

        try:
            result = extract_from_page(
                db=db,
                run_id=page.run_id,
                source_url=page.url,
                page_text=page.content_markdown,
            )
        except OverBudget as exc:
            return {"status": "over_budget", "reason": str(exc)}

        if result.error and not result.organizations:
            return {
                "status": "extraction_failed",
                "error": result.error,
                "cost_cents": result.cost.total_cents,
                "retried": result.retried,
            }

        counts = _persist(db, page=page, result=result)
        return {
            "status": "ok",
            "cost_cents": result.cost.total_cents,
            "tokens": result.cost.total_tokens,
            "retried": result.retried,
            "extraction_error": result.error,
            **counts,
        }
    finally:
        db.close()
