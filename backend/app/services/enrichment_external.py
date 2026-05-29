"""External-API contact enrichment — waterfall scaffold for vendor chain.

The Perplexity Model Council convergence on this surface (see
tasks/contact_enrichment_council_synthesis.md) was that multiple
providers, queried in order, materially outperform any single vendor on
K-12 educator lookups. Underlying sources overlap but aren't perfectly
correlated.

This module exposes one entrypoint — `lookup_via_api(name, domain)` —
that runs a fixed chain of provider functions and returns the first
confident hit. Today every provider is stubbed and returns None, so
the function always returns None and the orchestrator continues with
crawl-only results. Wiring a real vendor is a one-function change:
replace the body of `_lookup_hunter` (or similar) with the vendor's
API call and translate to EnrichmentResult.

Chain order (intentionally curated, not alphabetical):

  1. Hunter — cheapest per-credit, transparent pricing, good for
     pattern discovery + verification. Best ROI for first procurement.
  2. Apollo (email-only) — broad coverage. Skip phone reveals — Apollo
     phone credits cost ~5× email credits and K-12 phone value is
     uncertain (Model Council convergence).
  3. RocketReach — third-line fallback, often resolves educator emails
     other vendors miss. Per-credit pricing.
  4. (Education-vertical providers MCH/Agile/MDR are NOT in this chain
     — their pricing is bulk-license, not per-lookup. They'd be used
     to ingest large CSVs into CRM's own cache, not for one-off
     enrichment lookups.)

Each provider function MUST:
  - return None on no-match, no-config, or any error (never raise)
  - log a one-line debug message so a chain trace is reconstructable
  - respect a per-call timeout so a slow vendor doesn't stall the chain

Catch-all SMTP handling lives in the validator integration (separate
chain element added when we wire NeverBounce/Kickbox/etc.) — vendors
return the candidate email, the validator decides if it's deliverable
or catch-all-risky.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import httpx

from app.config import settings
from app.services.email_pattern import split_name

log = logging.getLogger(__name__)

_HUNTER_EMAIL_FINDER = "https://api.hunter.io/v2/email-finder"


@dataclass(frozen=True)
class EnrichmentResult:
    """One enrichment hit. Empty fields mean the vendor didn't have that
    column for this person; consumers should treat them as 'still missing'."""

    email: str | None
    phone: str | None
    title: str | None
    linkedin_url: str | None
    source: str   # vendor name for audit (e.g. "hunter", "apollo")
    confidence: float  # 0..1, vendor-reported when available; 0.5 default


# Type alias for a provider lookup function. Same signature for every
# vendor stub so the chain runner stays trivial.
ProviderLookup = Callable[[str, str, str | None], EnrichmentResult | None]


# ---------- provider stubs (each returns None today) ----------


def _lookup_hunter(
    name: str, domain: str, title_hint: str | None
) -> EnrichmentResult | None:
    """Hunter.io email-finder. Email-only vendor — no phones.

    Returns None when no key is configured (default-off), when the name
    can't be split into first/last, or on any transport/API error.
    Hunter's `score` (0..100) becomes our confidence (score/100).
    """
    if not settings.hunter_api_key:
        log.debug("hunter: no key configured, skipping %s @ %s", name, domain)
        return None
    parts = split_name(name)
    if parts is None:
        return None
    params = {
        "domain": domain,
        "first_name": parts.first,
        "last_name": parts.last,
        "api_key": settings.hunter_api_key,
    }
    try:
        with httpx.Client(
            timeout=settings.enrichment_request_timeout_secs
        ) as cli:
            resp = cli.get(_HUNTER_EMAIL_FINDER, params=params)
    except httpx.HTTPError as exc:
        log.warning("hunter transport error for %s @ %s: %s", name, domain, exc)
        return None
    if resp.status_code >= 400:
        log.warning(
            "hunter http %d for %s @ %s: %s",
            resp.status_code,
            name,
            domain,
            resp.text[:200],
        )
        return None
    data = (resp.json() or {}).get("data") or {}
    email = data.get("email")
    if not email:
        return None
    score = data.get("score")
    confidence = (score / 100.0) if isinstance(score, (int, float)) else 0.5
    return EnrichmentResult(
        email=email,
        phone=None,
        title=data.get("position") or title_hint,
        linkedin_url=data.get("linkedin"),
        source="hunter",
        confidence=confidence,
    )


def _lookup_apollo(
    name: str, domain: str, title_hint: str | None
) -> EnrichmentResult | None:
    """Apollo.io people-search, email-only.

    Important: Apollo phone reveals cost ~5× the credits of email reveals.
    Per Model Council, K-12 phone enrichment value is low (main-line /
    extension noise), so this integration MUST NOT request phone numbers
    when wired. Use Apollo's people/match endpoint with
    `reveal_personal_emails=false` and skip the phone reveal call.
    """
    log.debug("apollo stub: %s @ %s — not configured", name, domain)
    return None


def _lookup_rocketreach(
    name: str, domain: str, title_hint: str | None
) -> EnrichmentResult | None:
    """RocketReach as third-line fallback. Often resolves educator emails
    that Hunter and Apollo miss; per-credit pricing keeps cost bounded."""
    log.debug("rocketreach stub: %s @ %s — not configured", name, domain)
    return None


# Chain order matters — cheapest, most accurate vendor first. Stop on
# first confident hit (confidence >= MIN_CONFIDENT).
_PROVIDER_CHAIN: tuple[ProviderLookup, ...] = (
    _lookup_hunter,
    _lookup_apollo,
    _lookup_rocketreach,
)

# A vendor result with confidence below this threshold is treated as a
# weak hit — chain continues to see if a later provider has stronger
# signal. Final result is the highest-confidence one found.
MIN_CONFIDENT = 0.7


# ---------- public entrypoint ----------


def lookup_via_api(
    name: str,
    domain: str,
    *,
    title_hint: str | None = None,
) -> EnrichmentResult | None:
    """Walk the provider chain for (name, domain). Returns the first hit
    at confidence >= MIN_CONFIDENT, OR the highest-confidence result
    across all providers if none cleared the threshold.

    Returns None when every provider returned None — the orchestrator
    treats that as "no external enrichment available" and falls back to
    crawl-only results.
    """
    best: EnrichmentResult | None = None
    for provider in _PROVIDER_CHAIN:
        try:
            result = provider(name, domain, title_hint)
        except Exception as exc:  # noqa: BLE001 — vendor SDK exceptions vary
            log.warning(
                "enrichment provider %s raised: %s",
                provider.__name__,
                exc,
            )
            continue
        if result is None:
            continue
        if result.confidence >= MIN_CONFIDENT:
            log.info(
                "enrichment hit via %s (confidence %.2f) for %s @ %s",
                result.source,
                result.confidence,
                name,
                domain,
            )
            return result
        # Below threshold — keep as fallback if no later provider beats it.
        if best is None or result.confidence > best.confidence:
            best = result
    if best is not None:
        log.info(
            "enrichment best-effort via %s (confidence %.2f, below threshold) for %s @ %s",
            best.source,
            best.confidence,
            name,
            domain,
        )
    return best
