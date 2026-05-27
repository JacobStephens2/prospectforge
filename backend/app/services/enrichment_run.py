"""Per-contact enrichment runs — "find contact info for this contact".

Given a Contact that's missing email or phone, an enrichment run:

  1. Resolves the contact's organization → website URL.
  2. Creates a one-off inactive Source seeded at that URL (so it doesn't
     pollute Morgan's source list but the run's lineage is real).
  3. Creates a ProspectRun with enrichment_for_contact_id set.
  4. Hands the run to the standard orchestrator. Any contacts the run
     produces get parent_contact_id stamped to the focus contact so
     the UI can display them as suggestions against the original.

The crawl reuses the full yield-improvement pipeline (deobfuscation,
PDF, sitemap, scoring, Tier-2 escalation). No new fetch logic — just a
targeted seed URL and a smaller default budget.

External-API fallback (Hunter, Apollo, ...) lives in
services.enrichment_external; this module calls it as a no-op today
and uses the result alongside crawler output once a vendor is wired.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Contact, Organization, ProspectRun, Source
from app.services.serp_search import find_person_page

log = logging.getLogger(__name__)


# Per-contact enrichment is a small, targeted crawl — much smaller than
# Morgan's broad source runs. Cap pages low and budget conservatively so
# a single misclick can't burn $5 on a single contact.
DEFAULT_ENRICHMENT_MAX_PAGES = 30
DEFAULT_ENRICHMENT_BUDGET_CENTS = 50  # $0.50 per per-contact lookup


class EnrichmentNotPossible(Exception):
    """Raised when we can't kick off an enrichment for a given contact —
    typically because the contact's org has no resolved domain/website."""


def _resolve_seed_url(org: Organization | None) -> str | None:
    """Pick the best URL to crawl for an enrichment. Prefer the org's
    explicit website, fall back to the inferred domain. Returns None when
    neither is available."""
    if org is None:
        return None
    if org.website:
        return org.website
    if org.domain:
        return f"https://{org.domain}/"
    return None


def start_enrichment_run(
    db: Session,
    *,
    contact_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    max_pages: int = DEFAULT_ENRICHMENT_MAX_PAGES,
    budget_cents: int = DEFAULT_ENRICHMENT_BUDGET_CENTS,
) -> ProspectRun:
    """Create + persist the enrichment ProspectRun. Returns the run row
    in 'pending' state; the caller is responsible for triggering the
    orchestrator (typically the API endpoint does this in a background
    thread, same as a normal /start).

    Raises EnrichmentNotPossible if the contact's org has no usable URL.
    """
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None:
        raise EnrichmentNotPossible(f"contact {contact_id} not found")

    org = None
    if contact.organization_id is not None:
        org = db.get(Organization, contact.organization_id)

    seed_url = _resolve_seed_url(org)
    if seed_url is None:
        raise EnrichmentNotPossible(
            "contact's organization has no website or domain — "
            "set one before requesting enrichment"
        )

    # SERP Tier-0: try to locate a single high-relevance page on the org
    # domain for this person before falling back to a broad crawl. When
    # no SERP provider is configured (default), find_person_page returns
    # None and we use the org-root seed unchanged.
    serp_seed = None
    domain_for_serp = org.domain if (org and org.domain) else None
    if domain_for_serp is None and seed_url:
        # Pull domain off the resolved URL as a fallback.
        from urllib.parse import urlparse as _urlparse
        domain_for_serp = _urlparse(seed_url).netloc or None
    if domain_for_serp:
        try:
            serp_seed = find_person_page(contact.name, domain_for_serp)
        except Exception as exc:  # noqa: BLE001 — never fail the run on SERP error
            log.warning("SERP Tier-0 errored for %s: %s", contact.name, exc)

    effective_seed = serp_seed or seed_url

    # One-off Source: inactive so it doesn't show in Morgan's source list
    # but the lineage (which run came from where) is preserved.
    source = Source(
        name=f"Enrichment: {contact.name}",
        url=effective_seed,
        crawl_tier=1,
        follow_external_links=False,
        max_external_pages=0,
        active=False,
        created_by=requested_by_user_id,
    )
    db.add(source)
    db.flush()

    run = ProspectRun(
        source_id=source.id,
        created_by=requested_by_user_id,
        max_cost_cents=budget_cents,
        budget_approved_by=requested_by_user_id,
        budget_approved_at=datetime.now(timezone.utc),
        enrichment_for_contact_id=contact.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    log.info(
        "enrichment run %s created for contact %s (seed=%s, serp_used=%s, budget=%d¢)",
        run.id,
        contact.id,
        effective_seed,
        serp_seed is not None,
        budget_cents,
    )
    return run


def list_enrichment_results(
    db: Session, *, parent_contact_id: uuid.UUID
) -> list[Contact]:
    """All contacts found by enrichment runs targeting this parent.

    Used by the UI to render 'suggestions' under the original contact.
    Ordered by created_at ascending so the chronological order of
    suggestions matches the order the user generated them.
    """
    return (
        db.query(Contact)
        .filter(Contact.parent_contact_id == parent_contact_id)
        .order_by(Contact.created_at.asc())
        .all()
    )


def list_enrichment_runs(
    db: Session, *, parent_contact_id: uuid.UUID
) -> list[ProspectRun]:
    """All runs that were started as enrichment for this contact.

    Lets the UI show 'enrichment in progress' state while the run is
    pending/running, and link to the run's page list for debugging."""
    return (
        db.query(ProspectRun)
        .filter(ProspectRun.enrichment_for_contact_id == parent_contact_id)
        .order_by(ProspectRun.created_at.desc())
        .all()
    )
