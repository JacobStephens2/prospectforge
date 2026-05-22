"""Run orchestrator — the loop that takes a pending ProspectRun and drives
it through crawl → extract → dedupe → enrichment → score → completed.

This is intentionally **synchronous** in the current shape. The right
production architecture is RQ-fanned: one job per URL, parallel extract
jobs, etc. But before fanning out we need to know the loop terminates
correctly on real data — building it sync first means we can run it
inside a TestClient + an integration test against the live Anthropic API
without the extra Redis/worker process complexity. Wiring this function
behind an RQ enqueue is a thin wrapper (workers/run_runner.py).

Steps:
  1. Discover URLs from the seed URL (same-domain shallow walk)
  2. Apply per-run caps: 1500 pages, 500 unique domains (spec §6.5)
  3. For each URL:
       a. robots.txt check (politeness, log+skip on Disallow)
       b. domain rate limit (2 req/sec)
       c. SSRF-guarded safe_fetch
       d. archive raw HTML (90-day retention)
       e. persist SourcePage
       f. extract_from_page (budget cap may halt the loop here)
       g. persist Organization + Contact rows
       h. dedupe contacts + organizations via CRM Bridge
       i. score each contact
  4. Mark run completed (or over_budget / failed).

Budget cap is checked inside extract; if it raises OverBudget we mark the
run and stop iterating — leftover pages stay as fetched-but-not-extracted
for a later re-run with more budget.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from app.models import (
    Contact,
    Organization,
    ProspectRun,
    Source,
    SourcePage,
)
from app.services.archive import write_raw_html
from app.services.budget import OverBudget
from app.services.enrichment import backfill_organization_domains, normalize_organization_website
from app.services.evidence import EvidenceCheck
from app.services.extraction import ExtractionRunResult, extract_from_page
from app.services.rate_limit import DomainRateLimiter
from app.services.robots import RobotsCache, USER_AGENT
from app.services.scoring import apply_score, score_contact
from app.services.ssrf_guard import SSRFBlocked
from app.services.crm_bridge import (
    lookup_contact_dedupe,
    lookup_organization_dedupe,
)
from app.services.directory_detect import score_directory_likelihood
from app.services.pagination_detect import discover_pagination_urls
from app.services.url_discovery import discover_links
from app.services.sitemap_discovery import discover_sitemap_urls
from app.services.spa_detect import should_auto_escalate
from app.services.url_scoring import rank_urls
from app.workers.crawl import HTTP_TIMEOUT_SECS, _metadata_with_error, safe_fetch
from app.services.firecrawl_client import map_site as firecrawl_map_site
from app.services.firecrawl_client import scrape as firecrawl_scrape
from app.services.ssrf_guard import validate_target_url

log = logging.getLogger(__name__)


# Run-level caps per spec §6.5.
MAX_PAGES_PER_RUN = 1500
MAX_DOMAINS_PER_RUN = 500
ARCHIVE_ROOT = "/var/lib/prospectforge/archive"
# Per-directory cap on dynamically-discovered paginator links. Bounded
# so a paginator that links to thousands of "?page=N" entries can't blow
# up the queue or budget.
MAX_PAGINATION_URLS_PER_DIRECTORY = 30


@dataclass
class RunSummary:
    pages_fetched: int
    pages_blocked: int
    organizations_extracted: int
    contacts_extracted: int
    contacts_blocked_dedupe: int
    contacts_blocked_evidence: int
    domains_backfilled: int
    final_status: str
    cost_cents: int
    error: str | None = None


def _set_run_status(db: Session, run: ProspectRun, status: str) -> None:
    run.status = status
    if status in ("completed", "failed", "over_budget"):
        run.completed_at = datetime.now(timezone.utc)
    db.commit()


def _safe_fetch_with_governance(
    *,
    url: str,
    client: httpx.Client,
    robots: RobotsCache,
    rate_limiter: DomainRateLimiter,
    crawl_tier: int = 1,
):
    """Apply robots + SSRF + rate limit, then dispatch to the right engine.

    Tier 1: httpx + trafilatura via safe_fetch (the SSRF guard runs inside
    safe_fetch as it follows redirects manually).

    Tier 2: self-hosted Firecrawl. Firecrawl runs Playwright internally
    and does its own redirect following, so we cannot re-validate each hop
    — instead we run our SSRF guard against the seed URL up-front. That's
    the same trust model as our outbound HTTP client's DNS resolution, but
    relies on Firecrawl not chasing redirects into private IP space (it
    doesn't, by default).
    """
    allowed, reason = robots.is_allowed(url, client=client)
    if not allowed:
        from app.workers.crawl import FetchResult  # local import to avoid cycle

        return FetchResult(
            url=url,
            canonical_url=None,
            http_status=None,
            status="blocked_robots",
            content_hash=None,
            content_markdown=None,
            page_metadata={"robots_reason": reason},
            error=reason,
        )
    rate_limiter.acquire(url)
    if crawl_tier == 2:
        # SSRF guard before handing the URL to Firecrawl. The guard raises
        # SSRFBlocked which the caller handles uniformly.
        validate_target_url(url)
        return firecrawl_scrape(url)
    return safe_fetch(url, client=client)


def _persist_page(
    db: Session,
    *,
    run_id: uuid.UUID,
    result,
    raw_body: bytes | None,
) -> SourcePage:
    """Write a SourcePage row (idempotent via the unique (run_id, url)
    constraint — if a page already exists, the IntegrityError is swallowed
    and we re-fetch the existing row)."""
    from sqlalchemy.exc import IntegrityError

    page = SourcePage(
        run_id=run_id,
        url=result.url,
        canonical_url=result.canonical_url,
        http_status=result.http_status,
        status=result.status,
        content_hash=result.content_hash,
        content_markdown=result.content_markdown,
        page_metadata=_metadata_with_error(result.page_metadata, result.error),
        fetched_at=datetime.now(timezone.utc) if result.status == "fetched" else None,
    )
    db.add(page)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(SourcePage)
            .filter(SourcePage.run_id == run_id, SourcePage.url == result.url)
            .first()
        )
        assert existing is not None, "unique constraint hit but row not found"
        return existing

    if result.status == "fetched" and raw_body:
        page.raw_html_path = write_raw_html(
            archive_root=ARCHIVE_ROOT,
            run_id=run_id,
            page_id=page.id,
            body=raw_body,
        )
        db.flush()
    db.commit()
    return page


def _apply_dedupe(
    db: Session,
    *,
    run_id: uuid.UUID,
    target_states: frozenset[str] | None = None,
) -> tuple[int, int]:
    """Stamp dedupe_status on contacts + organizations from this run.

    Returns (contacts_blocked, contacts_blocked_by_evidence). 'Blocked'
    here counts contacts whose dedupe outcome blocks export (suppressed,
    exact, domain_match→customer) — informational only.
    """
    contacts = db.query(Contact).filter(Contact.run_id == run_id).all()
    contacts_blocked = 0
    blocked_evidence = 0
    for c in contacts:
        if c.evidence_verified not in ("verified", "fuzzy"):
            blocked_evidence += 1
        org = None
        if c.organization_id is not None:
            org = db.get(Organization, c.organization_id)
        result = lookup_contact_dedupe(
            db,
            email=c.email,
            org_name=org.name if org else None,
            state=org.state if org else None,
        )
        c.dedupe_status = result.status
        c.dedupe_reason = result.reason
        if result.crm_agent_user_id is not None:
            c.crm_agent_user_id = result.crm_agent_user_id
        if result.crm_agency_id is not None:
            c.crm_agency_id = result.crm_agency_id
        if result.blocks_export:
            contacts_blocked += 1

    # Organization-level dedupe
    orgs = db.query(Organization).filter(Organization.run_id == run_id).all()
    for org in orgs:
        result = lookup_organization_dedupe(
            db,
            name=org.name,
            state=org.state,
            domain=org.domain,
        )
        org.dedupe_status = result.status
        org.dedupe_reason = result.reason
        if result.crm_agency_id is not None:
            org.crm_agency_id = result.crm_agency_id

    db.commit()
    return contacts_blocked, blocked_evidence


def _apply_scoring(
    db: Session,
    *,
    run_id: uuid.UUID,
    target_states: frozenset[str] | None,
) -> int:
    contacts = db.query(Contact).filter(Contact.run_id == run_id).all()
    scored = 0
    for c in contacts:
        org = db.get(Organization, c.organization_id) if c.organization_id else None
        score = score_contact(c, organization=org, target_states=target_states)
        apply_score(c, score)
        scored += 1
    if scored:
        db.commit()
    return scored


def execute_run(
    *,
    db: Session,
    run_id: uuid.UUID,
    max_pages: int = MAX_PAGES_PER_RUN,
    max_domains: int = MAX_DOMAINS_PER_RUN,
    target_states: frozenset[str] | None = None,
) -> RunSummary:
    """Drive a pending run to completion. Synchronous, single-process.

    Returns a RunSummary; the run row's final state mirrors it.
    """
    run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
    if run is None:
        raise ValueError(f"run {run_id} not found")
    if run.status not in ("pending",):
        raise ValueError(f"run is in status {run.status!r}, only 'pending' starts are allowed")
    source = db.query(Source).filter(Source.id == run.source_id).first()
    if source is None:
        raise ValueError(f"source {run.source_id} not found (orphan run)")

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()

    robots = RobotsCache()
    rate_limiter = DomainRateLimiter(rate_per_sec=2.0)
    client = httpx.Client(timeout=HTTP_TIMEOUT_SECS, headers={"User-Agent": USER_AGENT})

    pages_fetched = 0
    pages_blocked = 0
    contacts_extracted = 0
    orgs_extracted = 0
    seen_urls: set[str] = set()
    seen_domains: set[str] = set()
    error: str | None = None
    final_status = "completed"

    try:
        crawl_tier = source.crawl_tier or 1

        # ----- Step 1: fetch the seed URL -----
        result = _safe_fetch_with_governance(
            url=source.url,
            client=client,
            robots=robots,
            rate_limiter=rate_limiter,
            crawl_tier=crawl_tier,
        )
        page = _persist_page(db, run_id=run.id, result=result, raw_body=result.raw_body)
        seen_urls.add(source.url)
        seen_domains.add(urlparse(source.url).netloc.lower())
        # After redirects, the canonical host might differ from the seed
        # (e.g. www.northwind-old.example → northwind.example). Add the canonical host
        # to seen_domains so same-domain discovery scopes correctly.
        canonical_host: str | None = None
        if result.canonical_url:
            canonical_host = urlparse(result.canonical_url).netloc.lower()
            if canonical_host:
                seen_domains.add(canonical_host)
        if result.status == "fetched":
            pages_fetched += 1
        else:
            pages_blocked += 1

        # ----- Step 2: discover URLs from the seed page -----
        # Both tiers union three discovery methods so no single source is
        # load-bearing:
        #   1. Link extraction on the seed page's raw HTML (Tier-2 returns
        #      JS-rendered HTML so this still finds links the static fetcher
        #      would miss).
        #   2. sitemap.xml + robots.txt Sitemap: lines.
        #   3. Tier-2 only: Firecrawl /v2/map for SPA-discovered routes.
        # Order-preserving dedupe; the first signal a URL appears in wins
        # its position in the frontier.
        same_domain_urls: list[str] = []
        external_urls: list[str] = []
        base_for_discovery: str | None = None
        if result.status == "fetched":
            base_for_discovery = result.canonical_url or source.url
            seed_host = urlparse(base_for_discovery).netloc.lower()
            seen_same: set[str] = set()
            seen_external: set[str] = set()

            def _add_same(u: str) -> None:
                if u in seen_same:
                    return
                if len(same_domain_urls) >= max_pages - 1:
                    return
                same_domain_urls.append(u)
                seen_same.add(u)

            def _add_external(u: str) -> None:
                if u in seen_external:
                    return
                cap = source.max_external_pages if source.follow_external_links else 0
                if cap <= 0 or len(external_urls) >= cap:
                    return
                external_urls.append(u)
                seen_external.add(u)

            # 1) Link extraction from the seed's HTML (raw_body is set by
            #    safe_fetch for Tier-1 and by firecrawl_scrape for Tier-2).
            if result.raw_body:
                try:
                    html_text = result.raw_body.decode("utf-8", errors="replace")
                    link_same, link_ext = discover_links(
                        base_url=base_for_discovery,
                        html=html_text,
                        max_same=max_pages - 1,
                        max_external=(
                            source.max_external_pages
                            if source.follow_external_links
                            else 0
                        ),
                    )
                    for u in link_same:
                        _add_same(u)
                    for u in link_ext:
                        _add_external(u)
                    log.info(
                        "link discovery on %s → %d same-domain, %d external",
                        base_for_discovery,
                        len(link_same),
                        len(link_ext),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "link discovery failed for %s: %s", base_for_discovery, exc
                    )

            # 2) Sitemap + robots.txt recall booster.
            try:
                sitemap_urls = discover_sitemap_urls(
                    base_for_discovery,
                    client=client,
                    max_urls=max_pages,
                )
                before = len(same_domain_urls)
                for u in sitemap_urls:
                    _add_same(u)
                log.info(
                    "sitemap discovery on %s → +%d same-domain URLs",
                    base_for_discovery,
                    len(same_domain_urls) - before,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "sitemap discovery failed for %s: %s", base_for_discovery, exc
                )

            # 3) Firecrawl /v2/map for Tier-2 only (Tier-1 sites are static
            #    enough that the HTML link extraction covers the same ground).
            #    Falls back to the base domain when the sub-path returns
            #    empty — Firecrawl itself recommends this in the warning
            #    field when /map yields nothing for a sub-path.
            if crawl_tier == 2:
                try:
                    mapped = firecrawl_map_site(base_for_discovery, limit=max_pages)
                    if not mapped:
                        parsed = urlparse(base_for_discovery)
                        base_only = urlunparse(
                            (parsed.scheme, parsed.netloc, "/", "", "", "")
                        )
                        if base_only != base_for_discovery:
                            log.info(
                                "firecrawl /map empty for %s, retrying base %s",
                                base_for_discovery,
                                base_only,
                            )
                            mapped = firecrawl_map_site(base_only, limit=max_pages)
                    map_added_same = 0
                    map_added_ext = 0
                    for u in mapped:
                        host = urlparse(u).netloc.lower()
                        if host == seed_host:
                            before = len(same_domain_urls)
                            _add_same(u)
                            if len(same_domain_urls) > before:
                                map_added_same += 1
                        elif source.follow_external_links:
                            before = len(external_urls)
                            _add_external(u)
                            if len(external_urls) > before:
                                map_added_ext += 1
                    log.info(
                        "firecrawl /map on %s → +%d same-domain, +%d external",
                        base_for_discovery,
                        map_added_same,
                        map_added_ext,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "firecrawl map failed for %s: %s", base_for_discovery, exc
                    )

        # ----- Step 3: queue the seed for extraction -----
        pages_to_extract: list[SourcePage] = [page]

        # ----- Step 4: walk discovered URLs -----
        # Same-domain first, then external. We bias toward staying on the
        # source's site to find pagination + sub-pages, only spilling out
        # to external members once we've exhausted that tier. Within each
        # tier, rank by URL score so high-yield paths (staff/faculty/contact)
        # win the per-run budget before time-sensitive pages (news/calendar).
        same_domain_urls = rank_urls(same_domain_urls)
        external_urls = rank_urls(external_urls)
        # Index-based walk so paginator URLs discovered mid-walk can be
        # appended to the frontier dynamically and processed in subsequent
        # iterations of the same loop.
        all_to_walk: list[str] = list(same_domain_urls) + list(external_urls)
        escalations_used = 0
        i = 0
        while i < len(all_to_walk):
            url = all_to_walk[i]
            i += 1
            if url in seen_urls:
                continue
            if len(seen_urls) >= max_pages:
                break
            domain = urlparse(url).netloc.lower()
            if domain not in seen_domains and len(seen_domains) >= max_domains:
                continue
            seen_urls.add(url)
            seen_domains.add(domain)

            try:
                step = _safe_fetch_with_governance(
                    url=url,
                    client=client,
                    robots=robots,
                    rate_limiter=rate_limiter,
                    crawl_tier=crawl_tier,
                )
            except SSRFBlocked as exc:
                log.info("SSRF blocked %s: %s", url, exc)
                continue

            # Tier-2 auto-escalation: Tier-1 fetched but produced near-empty
            # markdown on a likely-SPA page that scored as a contact target.
            # Re-fetch via Firecrawl so the rendered DOM reaches extraction.
            if crawl_tier == 1 and step.status == "fetched" and should_auto_escalate(
                url=url,
                raw_body=step.raw_body,
                markdown=step.content_markdown,
                escalations_used=escalations_used,
            ):
                log.info("auto-escalating %s to Firecrawl (SPA shell)", url)
                try:
                    validate_target_url(url)
                    rate_limiter.acquire(url)
                    fc_result = firecrawl_scrape(url)
                    if fc_result.status == "fetched":
                        step = fc_result
                        escalations_used += 1
                except SSRFBlocked as exc:
                    log.info("SSRF blocked escalation for %s: %s", url, exc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("escalation failed for %s: %s", url, exc)

            step_page = _persist_page(db, run_id=run.id, result=step, raw_body=step.raw_body)
            if step.status == "fetched":
                pages_fetched += 1
                pages_to_extract.append(step_page)

                # Directory pagination expansion (NCES-style): if this page
                # looks like a staff directory, harvest paginator URLs and
                # append to the frontier so subsequent iterations crawl them.
                if step.raw_body:
                    try:
                        title = (
                            step.page_metadata.get("title")
                            if step.page_metadata
                            else None
                        )
                        dir_score = score_directory_likelihood(
                            title=title,
                            body_prefix=step.content_markdown,
                        )
                        if dir_score.is_directory:
                            raw = step.raw_body.decode("utf-8", errors="replace")
                            paginators = discover_pagination_urls(
                                step.canonical_url or url,
                                raw,
                                max_urls=MAX_PAGINATION_URLS_PER_DIRECTORY,
                            )
                            added = 0
                            for p_url in paginators:
                                if p_url in seen_urls or p_url in all_to_walk:
                                    continue
                                all_to_walk.append(p_url)
                                added += 1
                            if added:
                                log.info(
                                    "directory page %s → +%d paginator URLs",
                                    url,
                                    added,
                                )
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "pagination expansion failed for %s: %s", url, exc
                        )
            else:
                pages_blocked += 1
            run.pages_crawled = pages_fetched
            db.commit()

        run.pages_crawled = pages_fetched
        db.commit()

        # ----- Step 5: extract from each fetched page -----
        contacts_blocked_evidence = 0
        for p in pages_to_extract:
            if not p.content_markdown:
                continue
            try:
                extraction = extract_from_page(
                    db=db,
                    run_id=run.id,
                    source_url=p.url,
                    page_text=p.content_markdown,
                )
            except OverBudget as exc:
                error = f"over budget after {pages_fetched} pages: {exc}"
                final_status = "over_budget"
                log.warning(error)
                break

            # Persist organizations + verified contacts (lifted from workers/extract.py)
            from app.workers.extract import _persist as _persist_extraction

            counts = _persist_extraction(db, page=p, result=extraction)
            orgs_extracted += counts["orgs_written"]
            contacts_extracted += counts["contacts_written"]
            contacts_blocked_evidence += counts["contacts_needs_review"]

        # ----- Step 6: enrich (website backfill) -----
        orgs_in_run = db.query(Organization).filter(Organization.run_id == run.id).all()
        backfilled = backfill_organization_domains(db, organizations=orgs_in_run)
        normalize_organization_website(db, organizations=orgs_in_run)

        # ----- Step 7: dedupe -----
        contacts_blocked_dedupe, _ev = _apply_dedupe(
            db, run_id=run.id, target_states=target_states
        )

        # ----- Step 8: score -----
        _apply_scoring(db, run_id=run.id, target_states=target_states)

    except Exception as exc:  # noqa: BLE001
        error = f"{exc.__class__.__name__}: {exc}"
        final_status = "failed"
        log.exception("run %s failed", run.id)
        contacts_blocked_dedupe = 0
        contacts_blocked_evidence = 0
        backfilled = 0

    finally:
        client.close()

    # Final status write — refresh after possible commits in helpers.
    db.refresh(run)
    _set_run_status(db, run, final_status if final_status != "running" else "completed")

    return RunSummary(
        pages_fetched=pages_fetched,
        pages_blocked=pages_blocked,
        organizations_extracted=orgs_extracted,
        contacts_extracted=contacts_extracted,
        contacts_blocked_dedupe=contacts_blocked_dedupe if final_status != "failed" else 0,
        contacts_blocked_evidence=contacts_blocked_evidence if final_status != "failed" else 0,
        domains_backfilled=backfilled if final_status != "failed" else 0,
        final_status=run.status,
        cost_cents=run.cost_cents,
        error=error,
    )
