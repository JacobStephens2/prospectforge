"""Enrichment waterfall — provider chain runner behavior."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import httpx

from app.services.enrichment_external import (
    MIN_CONFIDENT,
    EnrichmentResult,
    _best_rocketreach_email,
    _lookup_apollo,
    _lookup_rocketreach,
    lookup_via_api,
)


def _mock_client(handler):
    """Patch httpx.Client so a provider's outbound call hits a MockTransport
    instead of the network. Returns a context-manager patch target."""
    from unittest.mock import patch

    real_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)

    class _Factory:
        def __call__(self, *args, **kwargs):
            return real_client

    return patch("app.services.enrichment_external.httpx.Client", _Factory())


def _result(source: str, confidence: float) -> EnrichmentResult:
    return EnrichmentResult(
        email=f"x@y.test",
        phone=None,
        title=None,
        linkedin_url=None,
        source=source,
        confidence=confidence,
    )


def test_all_stubs_return_none_by_default():
    """Default state: every provider is a stub, function returns None."""
    assert lookup_via_api("Jane Smith", "school.edu") is None


def test_first_confident_hit_short_circuits():
    """Walker stops as soon as a provider returns confidence >= threshold."""
    hunter_hit = _result("hunter", 0.95)
    apollo_called = {"n": 0}

    def apollo_stub(name, domain, title_hint):
        apollo_called["n"] += 1
        return _result("apollo", 0.99)

    with patch(
        "app.services.enrichment_external._PROVIDER_CHAIN",
        (lambda n, d, t: hunter_hit, apollo_stub),
    ):
        out = lookup_via_api("Jane", "x.edu")
    assert out is hunter_hit
    assert apollo_called["n"] == 0, "chain should short-circuit on first confident hit"


def test_weak_hits_keep_walking_and_return_best():
    """All providers return below-threshold results → return the strongest."""
    weak = _result("hunter", 0.3)
    weaker = _result("rocketreach", 0.1)
    medium = _result("apollo", 0.5)

    with patch(
        "app.services.enrichment_external._PROVIDER_CHAIN",
        (
            lambda n, d, t: weak,
            lambda n, d, t: medium,
            lambda n, d, t: weaker,
        ),
    ):
        out = lookup_via_api("Jane", "x.edu")
    assert out is medium


def test_provider_exceptions_are_swallowed_and_chain_continues():
    """A misbehaving vendor SDK must not abort the chain."""
    hunter_hit = _result("hunter", 0.95)

    def boom(name, domain, title_hint):
        raise RuntimeError("vendor SDK exploded")

    with patch(
        "app.services.enrichment_external._PROVIDER_CHAIN",
        (boom, lambda n, d, t: hunter_hit),
    ):
        out = lookup_via_api("Jane", "x.edu")
    assert out is hunter_hit


def test_none_from_provider_skips_to_next():
    apollo_hit = _result("apollo", 0.92)
    with patch(
        "app.services.enrichment_external._PROVIDER_CHAIN",
        (lambda n, d, t: None, lambda n, d, t: apollo_hit),
    ):
        out = lookup_via_api("Jane", "x.edu")
    assert out is apollo_hit


def test_threshold_constant_is_sensible():
    """Smoke test — threshold shouldn't drift to extremes by accident."""
    assert 0.5 <= MIN_CONFIDENT <= 0.95


# ---------- Apollo ----------


def test_apollo_returns_none_without_key():
    with patch("app.services.enrichment_external.settings.apollo_api_key", ""):
        assert _lookup_apollo("Jane Doe", "school.edu", None) is None


def test_apollo_parses_revealed_email():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane.doe@school.edu",
                    "email_status": "verified",
                    "title": "Director of Admissions",
                    "linkedin_url": "https://linkedin.com/in/jane",
                }
            },
        )

    with patch("app.services.enrichment_external.settings.apollo_api_key", "k"), _mock_client(handler):
        out = _lookup_apollo("Jane Doe", "school.edu", None)
    assert out is not None
    assert out.email == "jane.doe@school.edu"
    assert out.source == "apollo"
    assert out.phone is None
    assert out.confidence == 0.85


def test_apollo_locked_email_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"person": {"email": "email_not_unlocked@domain.com"}})

    with patch("app.services.enrichment_external.settings.apollo_api_key", "k"), _mock_client(handler):
        assert _lookup_apollo("Jane Doe", "school.edu", None) is None


def test_apollo_http_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    with patch("app.services.enrichment_external.settings.apollo_api_key", "k"), _mock_client(handler):
        assert _lookup_apollo("Jane Doe", "school.edu", None) is None


# ---------- RocketReach ----------


def test_rocketreach_returns_none_without_key():
    with patch("app.services.enrichment_external.settings.rocketreach_api_key", ""):
        assert _lookup_rocketreach("Jane Doe", "school.edu", None) is None


def test_rocketreach_picks_on_domain_valid_email():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["api-key"] == "k"
        return httpx.Response(
            200,
            json={
                "current_title": "Teacher",
                "linkedin_url": "https://linkedin.com/in/jd",
                "emails": [
                    {"email": "jdoe@other.com", "smtp_valid": "valid"},
                    {"email": "jane.doe@school.edu", "smtp_valid": "valid"},
                ],
            },
        )

    with patch("app.services.enrichment_external.settings.rocketreach_api_key", "k"), _mock_client(handler):
        out = _lookup_rocketreach("Jane Doe", "school.edu", None)
    assert out is not None
    assert out.email == "jane.doe@school.edu"
    assert out.confidence == 0.85
    assert out.source == "rocketreach"


def test_best_rocketreach_email_prefers_on_domain_valid():
    emails = [
        {"email": "x@elsewhere.com", "smtp_valid": "valid"},
        {"email": "jane@school.edu", "smtp_valid": "valid"},
    ]
    addr, conf = _best_rocketreach_email(emails, "school.edu")
    assert addr == "jane@school.edu"
    assert conf == 0.85


def test_best_rocketreach_email_empty_list():
    assert _best_rocketreach_email([], "school.edu") == (None, 0.0)


def test_rocketreach_falls_back_to_recommended_professional_email():
    """When emails[] yields nothing usable, RR's curated
    `recommended_professional_email` is preferred over `current_work_email`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "current_title": "Principal",
                "emails": [],
                "recommended_professional_email": "jane.doe@school.edu",
                "current_work_email": "jdoe@school-old.edu",
            },
        )

    with patch("app.services.enrichment_external.settings.rocketreach_api_key", "k"), _mock_client(handler):
        out = _lookup_rocketreach("Jane Doe", "school.edu", None)
    assert out is not None
    assert out.email == "jane.doe@school.edu"
    assert out.confidence == 0.7  # on-domain recommended


def test_rocketreach_polls_async_status_until_complete(monkeypatch):
    """Fresh lookup returns status=progress; code polls checkStatus then
    re-fetches the lookup to get the enriched emails."""
    monkeypatch.setattr(
        "app.services.enrichment_external._ROCKETREACH_POLL_DELAY_SECS", 0.0
    )
    calls = {"lookup": 0, "status": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "checkStatus" in str(request.url):
            calls["status"] += 1
            return httpx.Response(200, json=[{"id": 42, "status": "complete"}])
        calls["lookup"] += 1
        if calls["lookup"] == 1:
            return httpx.Response(
                200,
                json={"id": 42, "status": "progress", "emails": []},
            )
        return httpx.Response(
            200,
            json={
                "id": 42,
                "status": "complete",
                "emails": [
                    {"email": "jane@school.edu", "smtp_valid": "valid"},
                ],
            },
        )

    with patch("app.services.enrichment_external.settings.rocketreach_api_key", "k"), _mock_client(handler):
        out = _lookup_rocketreach("Jane Doe", "school.edu", None)
    assert out is not None
    assert out.email == "jane@school.edu"
    assert calls["lookup"] == 2  # initial + post-complete re-fetch
    assert calls["status"] >= 1
