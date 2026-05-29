"""Post-crawl enrichment follow-up.

After the enrichment crawl runs, the target contact may still lack an
email even though the crawl found other people at the same org (with
emails). This step closes that gap:

  1. Gather verified (name, email) samples from the contacts the crawl
     produced on the target's domain.
  2. If >=N agree on a pattern, infer the target's likely email.
  3. If pattern inference yields nothing, fall back to the external
     vendor waterfall (Hunter, ...).
  4. Validate any candidate (NeverBounce). Drop INVALID outright; label
     VALID as 'verified', CATCHALL as 'unverifiable_catchall', UNKNOWN
     as 'pattern_guess'.
  5. Persist the candidate as a suggestion contact (parent_contact_id =
     target) so it surfaces through the existing review verdict UI — the
     operator still decides whether to apply it.

Everything degrades to a no-op when the relevant feature is unconfigured
(no SERP/vendor/validator keys) or no usable signal exists. Never raises
into the caller — enrichment must complete even if follow-up can't run.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Contact, Organization, ProspectRun
from app.services.email_pattern import generate_email, infer_pattern
from app.services.email_validate import (
    ValidationStatus,
    confidence_label_for,
    validate_email,
)
from app.services.enrichment_external import lookup_via_api

log = logging.getLogger(__name__)


def run_enrichment_in_background(run_id: uuid.UUID) -> None:
    """Thread target for enrichment runs: drive the crawl, then run the
    inference/vendor/validation follow-up. Mirrors routes.runs._run_in_background
    for crash handling (stamp the run failed if the crawl raises) but adds
    the post-crawl follow-up step that plain source runs don't need.
    """
    from app.services.run_orchestrator import execute_run

    db = SessionLocal()
    try:
        try:
            execute_run(db=db, run_id=run_id)
        except Exception:  # noqa: BLE001
            log.exception("enrichment crawl %s crashed", run_id)
            db.rollback()
            run = db.get(ProspectRun, run_id)
            if run is not None and run.status in ("pending", "running"):
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
            return
        # Crawl finished — run the follow-up. Never let a follow-up error
        # bubble up; the crawl results are already persisted and valid.
        try:
            run_enrichment_followup(db, run_id)
        except Exception:  # noqa: BLE001
            log.exception("enrichment follow-up for %s failed", run_id)
    finally:
        db.close()


def _norm(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split())


def _domain_from_samples(samples: list[tuple[str, str]]) -> str | None:
    counts: Counter[str] = Counter()
    for _name, email in samples:
        if email and "@" in email:
            counts[email.split("@", 1)[1].strip().lower()] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def run_enrichment_followup(db: Session, run_id: uuid.UUID) -> dict[str, object]:
    """Run the inference/vendor/validation follow-up for one enrichment run.
    Returns a small dict describing what happened (for logging/tests).
    Idempotent-ish: if a same-name suggestion with an email already exists
    for the target, we skip (the crawl already found something)."""
    run = db.get(ProspectRun, run_id)
    if run is None or run.enrichment_for_contact_id is None:
        return {"status": "not_an_enrichment_run"}
    target = db.get(Contact, run.enrichment_for_contact_id)
    if target is None:
        return {"status": "target_missing"}
    if target.email:
        return {"status": "target_already_has_email"}

    target_norm = _norm(target.name)

    # Did the crawl already surface the target's own email as a suggestion?
    existing = (
        db.query(Contact)
        .filter(
            Contact.parent_contact_id == target.id,
            Contact.email.isnot(None),
        )
        .all()
    )
    if any(_norm(c.name) == target_norm for c in existing):
        return {"status": "crawl_already_found_self"}

    # Verified (name, email) samples from this run's extracted contacts.
    run_contacts = (
        db.query(Contact)
        .filter(Contact.run_id == run_id, Contact.email.isnot(None))
        .all()
    )
    samples = [(c.name, c.email) for c in run_contacts if c.email]

    org = (
        db.get(Organization, target.organization_id)
        if target.organization_id
        else None
    )
    domain = (org.domain if org and org.domain else None) or _domain_from_samples(
        samples
    )

    candidate_email: str | None = None
    candidate_source: str | None = None

    # 1. Pattern inference.
    if settings.enrichment_pattern_inference_enabled and samples:
        pat = infer_pattern(
            samples, min_samples=settings.enrichment_pattern_min_samples
        )
        if pat is not None:
            candidate_email = generate_email(target.name, pat)
            if candidate_email:
                candidate_source = f"pattern:{pat.pattern}"

    # 2. Vendor waterfall fallback.
    if candidate_email is None and domain:
        vendor = lookup_via_api(target.name, domain, title_hint=target.title)
        if vendor is not None and vendor.email:
            candidate_email = vendor.email
            candidate_source = vendor.source

    if candidate_email is None:
        return {"status": "no_candidate"}

    # 3. Validate.
    validation = validate_email(candidate_email)
    if validation == ValidationStatus.INVALID:
        log.info(
            "enrichment followup: candidate %s for %s validated INVALID — dropping",
            candidate_email,
            target.name,
        )
        return {"status": "candidate_invalid", "candidate": candidate_email}
    confidence = confidence_label_for(validation)

    # 4. Persist as a suggestion contact under the target.
    suggestion = Contact(
        run_id=run_id,
        organization_id=target.organization_id,
        name=target.name,
        title=target.title,
        email=candidate_email,
        email_confidence=confidence,
        parent_contact_id=target.id,
        evidence_excerpt=(
            f"Email derived via {candidate_source}; validation={validation.value}. "
            f"Candidate: {candidate_email}"
        ),
        evidence_verified="unverified",
        review_status="needs_review",
    )
    db.add(suggestion)
    try:
        db.commit()
    except IntegrityError:
        # (run_id, lower(email)) collision — the email already exists in
        # this run. Harmless; the existing row wins.
        db.rollback()
        return {"status": "duplicate_candidate", "candidate": candidate_email}

    log.info(
        "enrichment followup: added %s candidate %s for %s (confidence=%s)",
        candidate_source,
        candidate_email,
        target.name,
        confidence,
    )
    return {
        "status": "candidate_added",
        "candidate": candidate_email,
        "source": candidate_source,
        "confidence": confidence,
        "validation": validation.value,
    }
