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

## Brave Search API ToS compliance

Brave's API terms prohibit storing, modifying, re-using, or re-selling
the raw SERP responses; AI inference on responses is permitted only
during the active subscription. We comply by:

  - **Extracting only the URL** from each response and discarding the
    rest immediately. The JSON body is never written to the DB, the
    archive, or any cache.
  - **Not implementing a SERP-response cache.** If you're tempted to
    add one to reduce API spend: don't. The contract is per-query at
    request time. If cost becomes an issue, the right move is to gate
    SERP behind a "has the contact's org domain been SERP'd in the
    last N days" check, where N tracks the *query*, not the response.
  - **Treating downstream extraction of contact info from the linked
    page as standard search use** — we visit a publicly-linked URL via
    our own crawler, which is independent of the SERP API itself.

Apply the same rules if you swap SerpAPI in: Google's ToS via SerpAPI
has comparable storage restrictions.
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


def _build_freesearch_query(name: str, org_name: str) -> str:
    """Strict free-form query when no org domain is known. Both name and org
    are phrase-quoted so the SERP treats them as exact phrases — strongest
    disambiguation signal between two people of the same name."""
    return f'"{name.strip()}" "{org_name.strip()}"'


def _build_freesearch_query_loose(name: str, org_name: str) -> str:
    """Looser variant for when the strict double-quoted query is too narrow
    (Brave returns bad_results=true on rare unindexed phrases). Keeps the
    name in quotes but drops quotes around the org name so it acts as
    relevance signal, not a hard filter."""
    return f'"{name.strip()}" {org_name.strip()}'


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


def _query_brave_raw(query: str) -> list[str]:
    """Issue any query string against Brave Web Search; return URLs in
    relevance order. Lower-level than _query_brave so callers can build
    site:domain queries or free-form queries through the same path."""
    if not settings.serp_brave_api_key:
        return []
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": settings.serp_brave_api_key,
    }
    params = {"q": query, "count": settings.serp_max_results_inspected}
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


def _query_serpapi_raw(query: str) -> list[str]:
    """SerpAPI counterpart to _query_brave_raw."""
    if not settings.serp_serpapi_api_key:
        return []
    params = {
        "engine": "google",
        "q": query,
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


def _query_brave(name: str, domain: str) -> list[str]:
    return _query_brave_raw(_build_query(name, domain))


def _query_serpapi(name: str, domain: str) -> list[str]:
    return _query_serpapi_raw(_build_query(name, domain))


def _query_freesearch(name: str, org_name: str) -> list[str]:
    """Dispatch a free-form name+org query through whichever SERP provider
    is configured. Tries the strict double-quoted query first; if it
    returns nothing, retries with the looser variant so we don't lose hits
    on rare unindexed phrasings (Brave returns bad_results=true on those).
    """
    if not name or not org_name:
        return []
    provider = settings.serp_provider.lower().strip()
    if provider not in ("brave", "serpapi"):
        return []
    dispatch = _query_brave_raw if provider == "brave" else _query_serpapi_raw

    urls = dispatch(_build_freesearch_query(name, org_name))
    if urls:
        return urls
    log.info(
        "SERP strict free-search empty for %r at %r — retrying loose",
        name,
        org_name,
    )
    return dispatch(_build_freesearch_query_loose(name, org_name))


def find_person_via_freesearch(name: str, org_name: str) -> str | None:
    """Free-form web search fallback for enrichment when the contact's
    organization has no known domain. Queries `"name" "org_name"` (both
    phrase-quoted for disambiguation) and returns the top result's URL.

    Returns None when no SERP provider is configured, when either input
    is empty, or when the SERP returned nothing.

    Caller responsibility: the returned URL may be on ANY domain
    (LinkedIn, news article, district website, etc.). Some of those are
    fine crawl targets, some are not (LinkedIn blocks bots). We don't
    filter here — the crawler tolerates getting nothing useful back.
    """
    if not name or not org_name:
        return None
    urls = _query_freesearch(name, org_name)
    if not urls:
        log.info(
            "SERP free-search returned no results for %r at %r",
            name,
            org_name,
        )
        return None
    pick = urls[0]
    log.info(
        "SERP free-search hit for %r at %r → %s",
        name,
        org_name,
        pick,
    )
    return pick


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
