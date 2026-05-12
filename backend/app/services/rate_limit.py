"""Per-domain token-bucket rate limiter.

Spec §6.5: max 2 req/sec/domain. Token bucket with refill at the configured
rate; `acquire()` blocks just long enough to respect the bucket. Per-domain
state so a slow target site doesn't impact crawling of fast ones in the
same run.

Process-local. If we ever scale to multiple worker processes hitting the
same domain, this needs to move to Redis — but a single backend process
handles thousands of pages just fine, so we punt that to v1.1.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class DomainRateLimiter:
    """One bucket per netloc. Default 2 req/sec, burst of 2.

    Usage:
        rl = DomainRateLimiter(rate_per_sec=2.0)
        rl.acquire("https://example.com/foo")  # sleeps if needed
    """

    def __init__(self, rate_per_sec: float = 2.0, burst: int | None = None) -> None:
        self._rate = float(rate_per_sec)
        self._burst = float(burst if burst is not None else max(1, int(rate_per_sec)))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def _domain_of(self, url: str) -> str:
        netloc = urlparse(url).netloc
        return netloc.lower()

    def acquire(self, url: str, *, _sleep=time.sleep, _now=time.monotonic) -> float:
        """Block until a token is available for url's domain. Returns the
        wait time in seconds (0 if no wait needed). `_sleep` / `_now` are
        test seams — tests inject deterministic clocks."""
        domain = self._domain_of(url)
        with self._lock:
            now = _now()
            bucket = self._buckets.get(domain)
            if bucket is None:
                bucket = _Bucket(tokens=self._burst, last_refill=now)
                self._buckets[domain] = bucket
            else:
                elapsed = now - bucket.last_refill
                bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
                bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return 0.0

            # Need to wait for one full token's worth of time.
            wait = (1.0 - bucket.tokens) / self._rate
            bucket.tokens = 0.0
            bucket.last_refill = now + wait

        # Sleep OUTSIDE the lock so concurrent acquires for OTHER domains
        # don't pile up on us.
        if wait > 0:
            _sleep(wait)
        return wait
