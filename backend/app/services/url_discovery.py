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
_SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|jpg|jpeg|png|gif|svg|webp|css|js|zip|tar|gz|mp3|mp4|mov|avi|ico)(\?|$)",
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
    base = urlparse(base_url)
    if not base.netloc:
        return []

    parser = _LinkExtractor()
    parser.feed(html)
    parser.close()

    seen: dict[str, None] = {}
    for href in parser.links:
        # Strip fragment, skip mailto:/tel:/javascript:.
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        joined = urljoin(base_url, href).split("#", 1)[0]
        target = urlparse(joined)
        if target.scheme not in ("http", "https"):
            continue
        if target.netloc.lower() != base.netloc.lower():
            continue
        if _SKIP_EXTENSIONS.search(target.path):
            continue
        if joined in seen:
            continue
        seen[joined] = None
        if len(seen) >= max_urls:
            break
    return list(seen.keys())
