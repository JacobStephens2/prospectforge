"""Tier-2 crawl engine: self-hosted Firecrawl via HTTP.

Sources marked `crawl_tier=2` route through Firecrawl instead of the Tier-1
httpx + trafilatura path. Firecrawl runs Playwright internally, so we get
JS-rendered content out of pages that the static fetcher returns near-empty
markup for (member lookup widgets, SPA-based directory listings, etc).

Self-hosted Firecrawl, configured with `USE_DB_AUTHENTICATION=false`, has no
auth header — it's only reachable on the docker-compose network. The two
endpoints we exercise:

  POST /v2/scrape  → fetch one URL, return markdown + html + metadata
  POST /v2/map     → enumerate URLs on a site (replaces our discover_links)

Outputs are normalized into the same `FetchResult` shape the Tier-1 path
produces, so the run orchestrator's persistence/extraction steps stay
oblivious to which tier produced the page.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.workers.crawl import FetchResult

log = logging.getLogger(__name__)


class FirecrawlError(RuntimeError):
    """Raised when the Firecrawl server returns a non-2xx or malformed body.

    The orchestrator catches this and stamps the page as `error`, so a
    Firecrawl outage doesn't kill the whole run — pages just won't extract.
    """


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.firecrawl_base_url,
        timeout=settings.firecrawl_request_timeout_secs,
    )


def scrape(url: str, *, client: httpx.Client | None = None) -> FetchResult:
    """Tier-2 fetch: POST to /v2/scrape, return a FetchResult.

    Firecrawl handles redirects, JS, and content extraction itself. We
    request markdown (fed straight to the extractor) plus rawHtml (used
    for link discovery and archived as the raw body).
    """
    own = client is None
    if client is None:
        client = _client()

    payload = {
        "url": url,
        # rawHtml (full rendered DOM), not html (cleaned/readability): the
        # cleaned variant strips nav menus, which is where CMS sites keep
        # their directory links — feeding it to discover_links() starved
        # Tier-2 frontiers to ~1 link (leonschools.net: 1 vs 626).
        "formats": ["markdown", "rawHtml"],
        # Skip waiting on a network-quiet signal beyond the default; Playwright
        # already handles "page loaded" reasonably. If we hit problems with
        # late-loading directories we can add `waitFor: 2000` here.
    }

    try:
        try:
            response = client.post("/v2/scrape", json=payload)
        except httpx.HTTPError as exc:
            return FetchResult(
                url=url,
                canonical_url=None,
                http_status=None,
                status="error",
                content_hash=None,
                content_markdown=None,
                page_metadata={"firecrawl_error": str(exc)},
                raw_body=None,
                error=f"firecrawl transport: {exc}",
            )

        if response.status_code >= 400:
            return FetchResult(
                url=url,
                canonical_url=None,
                http_status=response.status_code,
                status="error",
                content_hash=None,
                content_markdown=None,
                page_metadata={"firecrawl_status": response.status_code},
                raw_body=None,
                error=f"firecrawl http {response.status_code}: {response.text[:300]}",
            )

        body = response.json()
        # v2 wraps the payload in {"success": true, "data": {...}}; older /v1
        # used a flat shape. Tolerate both so a future schema bump in the
        # upstream image doesn't break the client.
        data = body.get("data") if isinstance(body.get("data"), dict) else body

        markdown: str | None = data.get("markdown")
        html: str | None = data.get("rawHtml") or data.get("html")
        metadata = data.get("metadata") or {}

        # Firecrawl metadata exposes the final URL and the upstream HTTP
        # status; mirror them into our FetchResult so the SourcePage row
        # still records the canonical URL after redirects.
        canonical = (
            metadata.get("sourceURL")
            or metadata.get("url")
            or metadata.get("ogUrl")
            or url
        )
        http_status = metadata.get("statusCode") or metadata.get("status")

        raw_body = html.encode("utf-8") if html else None
        content_hash = _hash(html or markdown or "") if (html or markdown) else None

        return FetchResult(
            url=url,
            canonical_url=canonical,
            http_status=int(http_status) if isinstance(http_status, int) else None,
            status="fetched",
            content_hash=content_hash,
            content_markdown=markdown,
            page_metadata={
                "tier": 2,
                "engine": "firecrawl",
                "title": metadata.get("title"),
                "description": metadata.get("description"),
                "language": metadata.get("language"),
                "firecrawl_status": http_status,
            },
            raw_body=raw_body,
        )
    finally:
        if own:
            client.close()


def map_site(
    url: str,
    *,
    limit: int = 200,
    client: httpx.Client | None = None,
) -> list[str]:
    """Tier-2 discovery: POST to /v2/map, return a list of URLs found on the site.

    Replaces `discover_links()` for tier-2 sources. We bound the result to
    `limit` here so the run-level page cap is still authoritative; the same
    SSRF guard runs on each URL before fetch (see run_orchestrator).
    """
    own = client is None
    if client is None:
        client = _client()

    try:
        try:
            response = client.post("/v2/map", json={"url": url, "limit": limit})
        except httpx.HTTPError as exc:
            log.warning("firecrawl /v2/map transport failure for %s: %s", url, exc)
            return []

        if response.status_code >= 400:
            log.warning(
                "firecrawl /v2/map http %d for %s: %s",
                response.status_code,
                url,
                response.text[:300],
            )
            return []

        body = response.json()
        # Same v2 / v1 shape tolerance as scrape.
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        links = data.get("links") or data.get("urls") or []

        # v2 returns objects ({"url": "...", "title": "..."}); v1 returned strings.
        out: list[str] = []
        for item in links:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                out.append(item["url"])
            elif isinstance(item, str):
                out.append(item)
        return out[:limit]
    finally:
        if own:
            client.close()
