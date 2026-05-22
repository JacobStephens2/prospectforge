"""Pagination URL detection for staff directories (yield workstream step 7).

NCES paper observation: many K-12 staff directories paginate via A-Z
navigation ("ABC...Z") or numeric page links ("1 2 3 ... > showing 1-4
of 65 staff"). Page 2+ of a paginated directory contains contacts that
are entirely invisible to a single-fetch crawler.

This module finds those paginator URLs from a directory page's HTML so
the orchestrator can enqueue them. Detection is patterns-only — we look
at href values for the structural shapes pagination uses, not the link
text (which is often a single letter / number that's hard to validate
without a wider DOM scan).

What we detect today:
  - Query-string pagination: ?page=N, ?p=N, ?start=N, ?offset=N
  - Path-component pagination: /page/2/, /p/2
  - A-Z links: ?letter=A, ?ltr=A, /staff/A, /staff/letter/A

What we don't detect yet:
  - JS-driven infinite scroll (no DOM events to introspect)
  - "Load more" buttons that fetch JSON via AJAX
  - Multi-page-spanning hash fragments
For those cases the Tier-2 auto-escalation path catches the rendered
DOM and the regular link discovery picks up subsequent page URLs.
"""

from __future__ import annotations

import re
import string
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse


# Query-string keys that indicate a page index. Case-insensitive match.
_PAGE_QUERY_KEYS = {"page", "p", "start", "offset", "pg", "pagenum"}
# Query-string keys that indicate A-Z letter navigation.
_LETTER_QUERY_KEYS = {"letter", "ltr", "alpha", "alphabetic"}

# Path patterns that smell like pagination — /page/N or /p/N as a tail
# segment.
_PATH_PAGE_RE = re.compile(r"/(?:page|p)/\d+(?:/|$)", re.IGNORECASE)
# Single-letter tail segment after a likely-directory path token.
_PATH_LETTER_RE = re.compile(
    r"/(?:staff|faculty|directory|teachers|people|leadership)/(?:letter/)?([A-Za-z])(?:/|$)",
    re.IGNORECASE,
)


class _HrefCollector(HTMLParser):
    """Lightweight <a href> grabber — same approach as url_discovery,
    duplicated here to avoid cross-module coupling on a small helper."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for k, v in attrs:
            if k.lower() == "href" and v:
                self.hrefs.append(v.strip())


def _is_paginated_query(query: str) -> bool:
    """True if the query string has a page/letter key."""
    if not query:
        return False
    for k, _v in parse_qsl(query, keep_blank_values=True):
        kl = k.lower()
        if kl in _PAGE_QUERY_KEYS or kl in _LETTER_QUERY_KEYS:
            return True
    return False


def _is_paginated_path(path: str) -> bool:
    return bool(_PATH_PAGE_RE.search(path) or _PATH_LETTER_RE.search(path))


def discover_pagination_urls(
    base_url: str,
    html: str,
    *,
    max_urls: int = 50,
) -> list[str]:
    """Return absolute URLs that look like paginator destinations from the
    given page's HTML, restricted to the same hostname as base_url. Order-
    preserving dedupe; capped at max_urls so an evil page with a thousand
    fake paginator links doesn't blow up the queue.
    """
    parsed_base = urlparse(base_url)
    if not parsed_base.netloc:
        return []
    base_host = parsed_base.netloc.lower()

    parser = _HrefCollector()
    parser.feed(html)
    parser.close()

    seen: dict[str, None] = {}
    for href in parser.hrefs:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        joined = urljoin(base_url, href).split("#", 1)[0]
        target = urlparse(joined)
        if target.scheme not in ("http", "https"):
            continue
        if target.netloc.lower() != base_host:
            continue
        if not (_is_paginated_query(target.query) or _is_paginated_path(target.path)):
            continue
        # Normalize: empty trailing fragment already dropped above. Keep
        # the rest as-is so the orchestrator's dedupe key is exact.
        normalized = urlunparse(target)
        if normalized not in seen and len(seen) < max_urls:
            seen[normalized] = None
    return list(seen.keys())


def synthesize_az_pagination(base_directory_url: str) -> list[str]:
    """If the orchestrator believes a URL is an A-Z paginated directory
    but can't see the actual A-Z links in the HTML (common when the
    paginator is JS-rendered and Tier-1 missed it), it can synthesize
    candidate URLs A..Z via ?letter=A. These get filtered to 404s at
    fetch time — the cost of a few wasted fetches is low compared to
    the recall win.

    Synthesized URLs are returned in the same order as ASCII_UPPERCASE
    so the prefix of the alphabet (where most surnames cluster) is
    crawled first when budget is tight.
    """
    parsed = urlparse(base_directory_url)
    if not parsed.netloc:
        return []
    out: list[str] = []
    for letter in string.ascii_uppercase:
        # Always uses ?letter=X regardless of base query — most CMSes
        # accept it as a filter; ones that don't simply 404 and the
        # orchestrator skips on.
        new_query = f"letter={letter}"
        if parsed.query:
            new_query = parsed.query + "&" + new_query
        out.append(
            urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    new_query,
                    "",
                )
            )
        )
    return out
