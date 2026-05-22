"""URL discovery for Tier-1 crawling (spec §6.2).

Tier 1 = httpx + trafilatura. We don't have Firecrawl /map (deferred). The
shallow approach: fetch the seed URL, parse <a href>, filter to same-domain
HTTP(S) targets, dedupe, return up to N URLs.

Sufficient for directory-style pages (Northwind member listing, TTA directory,
school faculty page). When pilot sources turn out to need deeper crawling,
the entry point can swap in Firecrawl per-source via crawl_tier=2.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


class _LinkExtractor(HTMLParser):
    """Pull href values from <a> tags. Standard-library HTML parser; no
    BeautifulSoup dep needed for this little work."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for k, v in attrs:
            if k.lower() == "href" and v:
                self.links.append(v.strip())


# Strip anchors and obvious non-page resources we don't want to crawl.
# NOTE: .pdf is intentionally NOT excluded — board minutes, faculty
# rosters, conference programs are commonly PDFs and the crawl worker
# routes application/pdf bodies through services.pdf_extract.
_SKIP_EXTENSIONS = re.compile(
    r"\.(jpg|jpeg|png|gif|svg|webp|css|js|zip|tar|gz|mp3|mp4|mov|avi|ico)(\?|$)",
    re.IGNORECASE,
)


def discover_same_domain_urls(
    *,
    base_url: str,
    html: str,
    max_urls: int = 30,
) -> list[str]:
    """Return up to max_urls absolute URLs found in `html`, restricted to the
    same hostname as base_url. Order-preserving dedupe."""
    same, _ext = discover_links(base_url=base_url, html=html, max_same=max_urls, max_external=0)
    return same


def discover_links(
    *,
    base_url: str,
    html: str,
    max_same: int = 30,
    max_external: int = 0,
) -> tuple[list[str], list[str]]:
    """Split discovered <a href> targets into (same-domain, external) lists.

    Both lists are order-preserving and deduped. External links are useful
    for directory-style sources (Northwind/Global Learning's member-schools
    listing, for example) where the actual contacts live on each member's
    own site. Caller decides whether to follow externals — `max_external=0`
    keeps the historical same-domain-only behavior.
    """
    base = urlparse(base_url)
    if not base.netloc:
        return [], []

    parser = _LinkExtractor()
    parser.feed(html)
    parser.close()

    base_host = base.netloc.lower()
    same_seen: dict[str, None] = {}
    ext_seen: dict[str, None] = {}

    for href in parser.links:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        joined = urljoin(base_url, href).split("#", 1)[0]
        target = urlparse(joined)
        if target.scheme not in ("http", "https"):
            continue
        if not target.netloc:
            continue
        if _SKIP_EXTENSIONS.search(target.path):
            continue

        if target.netloc.lower() == base_host:
            if joined not in same_seen and len(same_seen) < max_same:
                same_seen[joined] = None
        else:
            if joined not in ext_seen and len(ext_seen) < max_external:
                ext_seen[joined] = None

        if len(same_seen) >= max_same and len(ext_seen) >= max_external:
            break

    return list(same_seen.keys()), list(ext_seen.keys())
