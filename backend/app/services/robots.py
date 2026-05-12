"""robots.txt politeness — fetch once per domain, cache the parser.

Spec §6.5: respect robots.txt. We fetch via the same SSRF-guarded path the
crawler uses for everything else (a malicious page can't redirect the
robots.txt fetch into the metadata endpoint).

Failure modes are deliberately permissive on transport errors (timeout,
DNS, 5xx) — we treat them as "no robots.txt published" rather than a hard
block. A site that doesn't return robots.txt isn't more restricted than
one that publishes an empty file. 4xx on the robots URL is also permissive.

What we DO honor: a parsed Disallow rule against the User-Agent we send.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.services.ssrf_guard import SSRFBlocked, validate_target_url

log = logging.getLogger(__name__)


# Same UA the crawler advertises so robots.txt decisions actually apply.
USER_AGENT = "ProspectForgeBot/0.1 (+https://acme-travel.example/prospectforge)"
_ROBOTS_TIMEOUT = 8.0


@dataclass
class _Entry:
    parser: RobotFileParser | None  # None = couldn't fetch; treat as allow-all
    fetched_at: float


class RobotsCache:
    """Per-domain cache. One per CrawlContext / Runner — not a singleton, so
    tests get a fresh cache and we don't carry state between runs."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = Lock()

    def is_allowed(
        self,
        url: str,
        *,
        client: httpx.Client | None = None,
        user_agent: str = USER_AGENT,
    ) -> tuple[bool, str]:
        """Returns (allowed, reason). Reason is empty when allowed."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "robots: unparseable URL"
        host = parsed.netloc
        with self._lock:
            entry = self._entries.get(host)
        if entry is None:
            entry = self._fetch(host, parsed.scheme, client=client)
            with self._lock:
                self._entries[host] = entry
        if entry.parser is None:
            return True, ""  # permissive on fetch failure
        if entry.parser.can_fetch(user_agent, url):
            return True, ""
        return False, f"robots: Disallow for {user_agent} at {url}"

    def _fetch(
        self,
        host: str,
        scheme: str,
        *,
        client: httpx.Client | None,
    ) -> _Entry:
        from time import time

        robots_url = f"{scheme}://{host}/robots.txt"
        # SSRF guard the robots URL too.
        try:
            validate_target_url(robots_url)
        except SSRFBlocked:
            log.warning("robots.txt blocked by SSRF guard for %s", host)
            return _Entry(parser=None, fetched_at=time())

        own_client = False
        if client is None:
            client = httpx.Client(timeout=_ROBOTS_TIMEOUT, headers={"User-Agent": USER_AGENT})
            own_client = True
        try:
            resp = client.get(robots_url, follow_redirects=False)
        except httpx.HTTPError as exc:
            log.debug("robots.txt fetch failed for %s: %s", host, exc)
            return _Entry(parser=None, fetched_at=time())
        finally:
            if own_client:
                client.close()

        if resp.status_code >= 400:
            return _Entry(parser=None, fetched_at=time())

        parser = RobotFileParser()
        try:
            parser.parse(resp.text.splitlines())
        except Exception as exc:  # malformed robots.txt — treat as allow-all
            log.warning("robots.txt parse failed for %s: %s", host, exc)
            return _Entry(parser=None, fetched_at=time())
        return _Entry(parser=parser, fetched_at=time())
