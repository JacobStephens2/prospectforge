"""Token-bucket rate limiter tests — uses injected clock/sleep so the
tests are deterministic and don't actually wait."""

from __future__ import annotations

from app.services.rate_limit import DomainRateLimiter


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def test_no_wait_for_first_n_calls_within_burst() -> None:
    clock = _FakeClock()
    rl = DomainRateLimiter(rate_per_sec=2.0, burst=2)
    assert rl.acquire("https://a.com/", _now=clock.now, _sleep=clock.sleep) == 0
    assert rl.acquire("https://a.com/", _now=clock.now, _sleep=clock.sleep) == 0
    assert clock.sleeps == []


def test_third_call_within_burst_waits() -> None:
    clock = _FakeClock()
    rl = DomainRateLimiter(rate_per_sec=2.0, burst=2)
    rl.acquire("https://a.com/", _now=clock.now, _sleep=clock.sleep)
    rl.acquire("https://a.com/", _now=clock.now, _sleep=clock.sleep)
    waited = rl.acquire("https://a.com/", _now=clock.now, _sleep=clock.sleep)
    # 1 token at 2/sec = 0.5s wait
    assert waited == 0.5
    assert clock.sleeps == [0.5]


def test_domains_are_independent() -> None:
    clock = _FakeClock()
    rl = DomainRateLimiter(rate_per_sec=2.0, burst=1)
    rl.acquire("https://a.com/x", _now=clock.now, _sleep=clock.sleep)
    rl.acquire("https://b.com/x", _now=clock.now, _sleep=clock.sleep)
    assert clock.sleeps == []  # different domains, no wait


def test_subdomains_count_as_separate_domains() -> None:
    """www.a.com and api.a.com are different netlocs — the rate limit is
    per netloc, not per registrable domain. This matches Python's urlparse."""
    clock = _FakeClock()
    rl = DomainRateLimiter(rate_per_sec=2.0, burst=1)
    rl.acquire("https://www.a.com/", _now=clock.now, _sleep=clock.sleep)
    rl.acquire("https://api.a.com/", _now=clock.now, _sleep=clock.sleep)
    assert clock.sleeps == []
