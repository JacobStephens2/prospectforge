"""Tier-1 crawl worker — httpx + trafilatura, SSRF-guarded.

This is the first-cut crawl primitive. Subsequent iterations layer in:
- robots.txt fetch + per-domain cache
- 2 req/sec/domain rate limit (token-bucket, lives in services/rate_limiter.py)
- 500 pages/run + 30 domains/run caps (run-level checks)
- Token-aware chunking for pages >10k tokens (services/chunking.py)
- Raw-HTML archive write to /var/lib/prospectforge/archive/ for 90-day retention

What's enforced here today:
- SSRF guard before every fetch AND after every redirect (spec §6.3)
- Manual redirect handling so each hop is re-validated (httpx's follow_redirects
  would skip our guard)
- Content-hash dedupe via SHA-256 of the body — if the same hash already
  exists in `source_pages` for this run, skip writing a duplicate page row
- Idempotency on (run_id, url) — relies on the unique constraint in 0001

Each call writes exactly one source_pages row (status reflects outcome), so the
worker is safe to retry: a failed fetch produces a row with status='error', a
retry inserts another error row only if the constraint allows it. The caller
(RQ scheduler) is expected to NOT retry idempotently-failed URLs without first
deleting the prior row.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import trafilatura

from app.db import SessionLocal
from app.models import SourcePage
from app.services.ssrf_guard import SSRFBlocked, validate_target_url

MAX_REDIRECTS = 5
HTTP_TIMEOUT_SECS = 60.0
USER_AGENT = "ProspectForgeBot/0.1 (+https://acme-travel.example/prospectforge)"


def _metadata_with_error(metadata: dict | None, error: str | None) -> dict | None:
    if not error:
        return metadata
    merged = dict(metadata or {})
    merged["error"] = error
    return merged



@dataclass
class FetchResult:
    """In-memory outcome of one fetch attempt. Persisted as a SourcePage."""

    url: str
    canonical_url: str | None
    http_status: int | None
    status: str
    # fetched | blocked_ssrf | blocked_redirect_limit | error
    content_hash: str | None
    content_markdown: str | None
    page_metadata: dict[str, Any]
    # Raw response body — kept in memory for the discovery step. Not
    # persisted to the DB; archive.write_raw_html() handles disk persistence.
    raw_body: bytes | None = None
    error: str | None = None


def _hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _extract_markdown(body: bytes, content_type: str | None) -> str | None:
    """Run trafilatura on HTML; bail on PDFs / images / etc."""
    if content_type and not content_type.lower().startswith(("text/", "application/xhtml")):
        return None
    try:
        return trafilatura.extract(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def safe_fetch(url: str, *, client: httpx.Client | None = None) -> FetchResult:
    """Fetch a URL through the SSRF guard with manual redirect re-validation.

    Returns a FetchResult; never raises (callers want a persistable record on
    every outcome). Created its own httpx.Client when one isn't provided so the
    function works as a standalone utility for one-off probes and tests.
    """
    own_client = False
    if client is None:
        client = httpx.Client(timeout=HTTP_TIMEOUT_SECS, headers={"User-Agent": USER_AGENT})
        own_client = True

    redirect_chain: list[str] = []
    current_url = url
    try:
        for hop in range(MAX_REDIRECTS + 1):
            try:
                target = validate_target_url(current_url)
            except SSRFBlocked as exc:
                return FetchResult(
                    url=url,
                    canonical_url=None,
                    http_status=None,
                    status="blocked_ssrf",
                    content_hash=None,
                    content_markdown=None,
                    page_metadata={
                        "ssrf_code": exc.code,
                        "ssrf_detail": exc.detail,
                        "redirect_chain": redirect_chain,
                        "blocked_at": current_url,
                    },
                    error=str(exc),
                )

            try:
                resp = client.get(current_url, follow_redirects=False)
            except httpx.HTTPError as exc:
                return FetchResult(
                    url=url,
                    canonical_url=current_url,
                    http_status=None,
                    status="error",
                    content_hash=None,
                    content_markdown=None,
                    page_metadata={
                        "redirect_chain": redirect_chain,
                        "resolved_ips": list(target.resolved_ips),
                    },
                    error=f"{exc.__class__.__name__}: {exc}",
                )

            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    return FetchResult(
                        url=url,
                        canonical_url=current_url,
                        http_status=resp.status_code,
                        status="error",
                        content_hash=None,
                        content_markdown=None,
                        page_metadata={"redirect_chain": redirect_chain},
                        error=f"{resp.status_code} redirect without Location header",
                    )
                redirect_chain.append(current_url)
                # urljoin handles relative redirects without us reimplementing
                # RFC 3986 reference resolution.
                from urllib.parse import urljoin

                current_url = urljoin(current_url, location)
                continue

            # Terminal response — non-redirect.
            body = resp.content
            content_type = resp.headers.get("content-type", "").split(";")[0].strip() or None
            return FetchResult(
                url=url,
                canonical_url=str(resp.url),
                http_status=resp.status_code,
                status="fetched",
                content_hash=_hash_body(body),
                content_markdown=_extract_markdown(body, content_type),
                raw_body=body,
                page_metadata={
                    "redirect_chain": redirect_chain,
                    "content_type": content_type,
                    "content_length": len(body),
                    "resolved_ips": list(target.resolved_ips),
                },
            )

        return FetchResult(
            url=url,
            canonical_url=current_url,
            http_status=None,
            status="blocked_redirect_limit",
            content_hash=None,
            content_markdown=None,
            page_metadata={"redirect_chain": redirect_chain},
            error=f"exceeded {MAX_REDIRECTS} redirects",
        )
    finally:
        if own_client:
            client.close()


def crawl_url(run_id: str, url: str) -> dict[str, Any]:
    """RQ entry point: fetch + persist a SourcePage. Returns the row's id.

    Idempotency: relies on the (run_id, url) unique constraint. A second call
    for the same (run_id, url) raises an IntegrityError from the DB, which RQ
    will surface to the run_jobs row.
    """
    run_uuid = uuid.UUID(run_id)
    result = safe_fetch(url)

    db = SessionLocal()
    try:
        page = SourcePage(
            run_id=run_uuid,
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
        db.commit()
        db.refresh(page)
        return {
            "id": str(page.id),
            "status": page.status,
            "http_status": page.http_status,
            "content_hash": page.content_hash,
        }
    finally:
        db.close()
