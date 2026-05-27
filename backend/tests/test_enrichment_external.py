"""Enrichment waterfall — provider chain runner behavior."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.enrichment_external import (
    MIN_CONFIDENT,
    EnrichmentResult,
    lookup_via_api,
)


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
