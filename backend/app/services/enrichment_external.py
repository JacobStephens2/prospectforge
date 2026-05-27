"""External-API contact enrichment — stubbed for procurement.

When the contact-info crawl on an org's own domain returns nothing
useful, the next-best signal source is a paid people-database (Hunter,
Apollo, RocketReach, ZoomInfo). This module is the integration point
for that fallback — `lookup_via_api(name, domain)` returns None today
and is wired into the enrichment orchestrator as a no-op.

To turn it on:
  1. Procure an account (the Perplexity Model Council yield-research
     prompt covers vendor evaluation criteria — see
     project memory project_yield_workstream).
  2. Add the API key to .env as ENRICH_API_KEY (or per-vendor key).
  3. Replace the body of `lookup_via_api` with the chosen vendor's
     SDK call and translate the response into EnrichmentResult.
  4. Add a settings flag (e.g. settings.enrichment_provider) to pick
     the vendor at runtime.

Why a stub vs a do-nothing config flag: keeping the call site stable
(the enrichment service always calls `lookup_via_api`) means the day
we turn it on we change exactly one file. No orchestrator surgery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichmentResult:
    """One enrichment hit. Empty fields mean the vendor didn't have that
    column for this person; consumers should treat them as 'still missing'."""

    email: str | None
    phone: str | None
    title: str | None
    linkedin_url: str | None
    source: str   # vendor name for audit
    confidence: float  # 0..1, vendor-reported when available; 0.5 default


def lookup_via_api(
    name: str,
    domain: str,
    *,
    title_hint: str | None = None,
) -> EnrichmentResult | None:
    """Look up a person by (name, domain) via an external enrichment API.

    Returns None when:
      - no vendor is configured (today's state)
      - the vendor returned no match
      - the vendor returned a low-confidence guess we don't want to ship

    Today this always returns None — see module docstring for activation.
    The orchestrator already handles None as "no external enrichment
    available" and continues with crawl-only results.
    """
    log.debug(
        "external enrichment stub called for %s @ %s (title_hint=%r) — not configured",
        name,
        domain,
        title_hint,
    )
    return None
