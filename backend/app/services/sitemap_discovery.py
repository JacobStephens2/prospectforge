"""Sitemap-based URL discovery (yield workstream step 3).

In addition to the seed-page link crawl, we look for URLs in:

  - /sitemap.xml at the root of the seed's domain
  - /sitemap_index.xml at the same root
  - any Sitemap: lines declared in robots.txt

These often surface deep staff/faculty pages that the shallow link crawl
(which only sees what the seed page links to) would never reach.

Sitemap indexes (<sitemapindex>...<sitemap><loc>...</loc>...) are fetched
recursively up to a small fan-out cap so a maliciously deep index can't
turn into thousands of round trips.

This is candidate generation only — every discovered URL still goes
through the SSRF guard, robots.txt check, rate limiter, dedupe, and
per-run page cap when the orchestrator decides to fetch it.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

from app.services.robots import USER_AGENT

log = logging.getLogger(__name__)


SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
DEFAULT_SITEMAP_TIMEOUT = 10.0
# Belt-and-suspenders against an evil sitemap-index loop: we fetch at most
# this many sitemap documents in one discovery call.
DEFAULT_MAX_SITEMAP_FETCHES = 10
DEFAULT_MAX_URLS = 1000


def _own_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return (
        httpx.Client(timeout=DEFAULT_SITEMAP_TIMEOUT, headers={"User-Agent": USER_AGENT}),
        True,
    )


def parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls). One of them is empty for any
    given document — sitemaps either list URLs or list child sitemaps.
    Returns ([], []) on malformed XML or unrecognized root element."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []

    tag = root.tag
    if tag == f"{SITEMAP_NS}urlset":
        urls = [
            loc.text.strip()
            for url in root.findall(f"{SITEMAP_NS}url")
            for loc in [url.find(f"{SITEMAP_NS}loc")]
            if loc is not None and loc.text
        ]
        return urls, []
    if tag == f"{SITEMAP_NS}sitemapindex":
        sitemaps = [
            loc.text.strip()
            for sm in root.findall(f"{SITEMAP_NS}sitemap")
            for loc in [sm.find(f"{SITEMAP_NS}loc")]
            if loc is not None and loc.text
        ]
        return [], sitemaps
    # Some sites omit the xmlns. Tolerate it via a namespace-less re-scan.
    if tag in ("urlset", "sitemapindex"):
        ns_prefix = ""
        if tag == "urlset":
            return (
                [
                    loc.text.strip()
                    for url in root.findall(f"{ns_prefix}url")
                    for loc in [url.find(f"{ns_prefix}loc")]
                    if loc is not None and loc.text
                ],
                [],
            )
        return (
            [],
            [
                loc.text.strip()
                for sm in root.findall(f"{ns_prefix}sitemap")
                for loc in [sm.find(f"{ns_prefix}loc")]
                if loc is not None and loc.text
            ],
        )
    return [], []


def _parse_robots_for_sitemaps(robots_text: str) -> list[str]:
    """Pull Sitemap: lines out of a robots.txt body. urllib.robotparser
    has .site_maps() but only after a successful parse — we don't want to
    re-parse here, so a one-line scan is fine."""
    out: list[str] = []
    for line in robots_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "sitemap":
            url = value.strip()
            if url:
                out.append(url)
    return out


def _fetch_text(url: str, client: httpx.Client) -> str | None:
    try:
        resp = client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.debug("sitemap fetch failed for %s: %s", url, exc)
        return None
    if resp.status_code >= 400:
        return None
    return resp.text


def discover_sitemap_urls(
    base_url: str,
    *,
    client: httpx.Client | None = None,
    max_urls: int = DEFAULT_MAX_URLS,
    max_sitemap_fetches: int = DEFAULT_MAX_SITEMAP_FETCHES,
) -> list[str]:
    """Discover candidate URLs for `base_url` from sitemaps + robots.txt.

    Returns up to `max_urls` URLs, order-preserving and deduped. Restricts
    to the same hostname as `base_url` — sitemap-declared cross-domain
    URLs almost always point at CDNs or off-site widgets we don't want
    to crawl. Returns [] on any error — sitemaps are an OPTIONAL recall
    booster, never a hard dependency.
    """
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return []
    host = parsed.netloc.lower()
    root = f"{parsed.scheme}://{parsed.netloc}"

    cli, own = _own_client(client)
    try:
        fetches_remaining = max_sitemap_fetches
        seen_sitemaps: set[str] = set()
        seen_urls: dict[str, None] = {}
        sitemap_queue: list[str] = []

        # 1) robots.txt Sitemap: lines (authoritative for the site).
        if fetches_remaining > 0:
            robots_text = _fetch_text(urljoin(root, "/robots.txt"), cli)
            fetches_remaining -= 1
            if robots_text:
                for sm in _parse_robots_for_sitemaps(robots_text):
                    if sm not in seen_sitemaps:
                        seen_sitemaps.add(sm)
                        sitemap_queue.append(sm)

        # 2) Conventional well-known names.
        for path in ("/sitemap.xml", "/sitemap_index.xml"):
            url = urljoin(root, path)
            if url not in seen_sitemaps:
                seen_sitemaps.add(url)
                sitemap_queue.append(url)

        # 3) Process the queue. urlset → collect; sitemapindex → enqueue children.
        while sitemap_queue and fetches_remaining > 0 and len(seen_urls) < max_urls:
            sm_url = sitemap_queue.pop(0)
            text = _fetch_text(sm_url, cli)
            fetches_remaining -= 1
            if not text:
                continue
            page_urls, child_sitemaps = parse_sitemap_xml(text)
            for u in page_urls:
                host_u = urlparse(u).netloc.lower()
                if host_u and host_u != host:
                    continue
                if u not in seen_urls and len(seen_urls) < max_urls:
                    seen_urls[u] = None
            for child in child_sitemaps:
                if child not in seen_sitemaps:
                    seen_sitemaps.add(child)
                    sitemap_queue.append(child)

        return list(seen_urls.keys())
    finally:
        if own:
            cli.close()
