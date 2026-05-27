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
from app.services.serp_search import find_person_page, find_person_via_freesearch

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

    # SERP Tier-0: try to locate a single high-relevance page on the org
    # domain for this person before falling back to a broad crawl. When
    # no SERP provider is configured (default), find_person_page returns
    # None and we use the org-root seed unchanged.
    serp_seed = None
    seed_source = "org_website"  # for log/audit clarity
    domain_for_serp = org.domain if (org and org.domain) else None
    if domain_for_serp is None and seed_url:
        # Pull domain off the resolved URL as a fallback.
        from urllib.parse import urlparse as _urlparse
        domain_for_serp = _urlparse(seed_url).netloc or None
    if domain_for_serp:
        try:
            serp_seed = find_person_page(contact.name, domain_for_serp)
            if serp_seed:
                seed_source = "serp_site"
        except Exception as exc:  # noqa: BLE001 — never fail the run on SERP error
            log.warning("SERP Tier-0 errored for %s: %s", contact.name, exc)

    # Free-form SERP fallback when there's NO seed URL at all (no org
    # website, no domain). Uses contact name + org name to find a
    # candidate page anywhere on the web. Per user request 2026-05-27:
    # don't 400 just because the org lacks a registered URL — go look.
    if seed_url is None and serp_seed is None and org is not None and org.name:
        try:
            serp_seed = find_person_via_freesearch(contact.name, org.name)
            if serp_seed:
                seed_source = "serp_freesearch"
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "SERP free-search errored for %s @ %s: %s",
                contact.name,
                org.name,
                exc,
            )

    effective_seed = serp_seed or seed_url
    if effective_seed is None:
        raise EnrichmentNotPossible(
            "no seed URL available — contact's organization has no website, "
            "no domain, and SERP free-search found nothing for the contact's "
            "name and organization. Set a website on the org to retry."
        )

    # One-off Source: inactive so it doesn't show in Morgan's source list
    # but the lineage (which run came from where) is preserved. The source
    # name encodes how the seed was found so reviewers can tell at a glance
    # whether the run was scoped to the org's own site or came from a
    # free-form web search.
    source_label = {
        "org_website": f"Enrichment: {contact.name}",
        "serp_site": f"Enrichment (SERP on org): {contact.name}",
        "serp_freesearch": f"Enrichment (web search): {contact.name}",
    }.get(seed_source, f"Enrichment: {contact.name}")
    source = Source(
        name=source_label,
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
        "enrichment run %s created for contact %s (seed=%s, seed_source=%s, budget=%d¢)",
        run.id,
        contact.id,
        effective_seed,
        seed_source,
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
