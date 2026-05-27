"""SERP Tier-0 for per-contact enrichment.

Per Perplexity Model Council (Claude unique finding): for the "find one
specific person" problem, a `site:domain "Full Name"` SERP query often
lands directly on the person's bio/staff page in one round trip — much
faster and more precise than crawling the whole org domain.

This module exposes `find_person_page(name, domain) -> URL | None`.
When a provider is configured (Brave or SerpAPI), it queries the SERP
and returns the highest-confidence same-domain result. When no provider
is set (default), returns None and the enrichment flow falls back to
the existing crawl behavior.

Why this isn't in the main `lookup_via_api` waterfall: this returns a
URL to crawl, not a contact record. It's a *locator* the crawler can
use, not a vendor providing the email directly.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.config import settings

log = logging.getLogger(__name__)


_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


def _build_query(name: str, domain: str) -> str:
    """Standard 'site:domain "Full Name"' query — quotes around the name
    so SERPs treat it as an exact phrase, not loose token matching."""
    # Strip schema/path from domain in case caller passes a URL.
    if "/" in domain:
        domain = urlparse(domain).netloc or domain.split("/")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return f'site:{domain} "{name.strip()}"'


def _filter_same_domain(urls: list[str], domain: str) -> list[str]:
    """Drop any URLs that aren't on the target domain. SERPs sometimes
    return tangential pages (cached copies, scrapers) we don't want."""
    if "/" in domain:
        domain = urlparse(domain).netloc or domain.split("/")[0]
    target = domain.lstrip("www.").lower()
    out: list[str] = []
    for u in urls:
        host = urlparse(u).netloc.lower().lstrip("www.")
        if host == target or host.endswith("." + target):
            out.append(u)
    return out


def _query_brave(name: str, domain: str) -> list[str]:
    if not settings.serp_brave_api_key:
        return []
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": settings.serp_brave_api_key,
    }
    params = {
        "q": _build_query(name, domain),
        "count": settings.serp_max_results_inspected,
    }
    try:
        with httpx.Client(timeout=settings.serp_request_timeout_secs) as cli:
            resp = cli.get(_BRAVE_ENDPOINT, headers=headers, params=params)
    except httpx.HTTPError as exc:
        log.warning("brave SERP transport failure: %s", exc)
        return []
    if resp.status_code >= 400:
        log.warning("brave SERP http %d: %s", resp.status_code, resp.text[:200])
        return []
    body = resp.json()
    web = body.get("web") or {}
    results = web.get("results") or []
    return [r.get("url") for r in results if r.get("url")]


def _query_serpapi(name: str, domain: str) -> list[str]:
    if not settings.serp_serpapi_api_key:
        return []
    params = {
        "engine": "google",
        "q": _build_query(name, domain),
        "num": settings.serp_max_results_inspected,
        "api_key": settings.serp_serpapi_api_key,
    }
    try:
        with httpx.Client(timeout=settings.serp_request_timeout_secs) as cli:
            resp = cli.get(_SERPAPI_ENDPOINT, params=params)
    except httpx.HTTPError as exc:
        log.warning("serpapi transport failure: %s", exc)
        return []
    if resp.status_code >= 400:
        log.warning("serpapi http %d: %s", resp.status_code, resp.text[:200])
        return []
    body = resp.json()
    results = body.get("organic_results") or []
    return [r.get("link") for r in results if r.get("link")]


def find_person_page(name: str, domain: str) -> str | None:
    """Return the most promising same-domain URL for (name, domain), or
    None when no provider is configured / no usable result was returned.

    Picks the FIRST same-domain result the SERP returned. SERPs already
    rank by relevance, so deeper re-ranking on our side adds little for
    single-person hunts. Future improvement: prefer URLs whose path
    matches the URL-score heuristic (staff/faculty/contact tokens).
    """
    if not name or not domain:
        return None
    provider = settings.serp_provider.lower().strip()
    if not provider:
        return None

    if provider == "brave":
        raw_urls = _query_brave(name, domain)
    elif provider == "serpapi":
        raw_urls = _query_serpapi(name, domain)
    else:
        log.warning("unknown SERP provider: %r", provider)
        return None

    same_domain = _filter_same_domain(raw_urls, domain)
    if not same_domain:
        log.info(
            "SERP returned no same-domain hits for %s @ %s (got %d off-domain)",
            name,
            domain,
            len(raw_urls),
        )
        return None
    pick = same_domain[0]
    log.info("SERP Tier-0 hit for %s @ %s → %s", name, domain, pick)
    return pick
