"""External-API contact enrichment — waterfall scaffold for vendor chain.

The Perplexity Model Council convergence on this surface (see
tasks/contact_enrichment_council_synthesis.md) was that multiple
providers, queried in order, materially outperform any single vendor on
K-12 educator lookups. Underlying sources overlap but aren't perfectly
correlated.

This module exposes one entrypoint — `lookup_via_api(name, domain)` —
that runs a fixed chain of provider functions and returns the first
confident hit. Hunter, Apollo, and RocketReach are all wired to their
real APIs; each is gated on its own API key (settings.*_api_key) and
returns None when its key is unset, so with no keys configured the
function returns None and the orchestrator continues crawl-only.
Activating a vendor is purely a matter of setting its key in .env.

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
_APOLLO_PEOPLE_MATCH = "https://api.apollo.io/api/v1/people/match"
_ROCKETREACH_LOOKUP = "https://api.rocketreach.co/api/v2/person/lookup"


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
    """Apollo.io people/match, email-only.

    Important: Apollo phone reveals cost ~5× the credits of email reveals.
    Per Model Council, K-12 phone enrichment value is low (main-line /
    extension noise), so this integration MUST NOT request phone numbers —
    we call people/match WITHOUT reveal_phone_number and skip any phone in
    the response.

    Auth is via the `X-Api-Key` header (Apollo's documented scheme); params
    go in the JSON body. Returns None on no-key, unsplittable name, any
    transport/API error, or a locked/un-revealed email placeholder.
    """
    if not settings.apollo_api_key:
        log.debug("apollo: no key configured, skipping %s @ %s", name, domain)
        return None
    parts = split_name(name)
    if parts is None:
        return None
    payload = {
        "first_name": parts.first,
        "last_name": parts.last,
        "domain": domain,
        # email-only: do NOT reveal phone (5× credit cost, low K-12 value)
        "reveal_personal_emails": False,
    }
    headers = {
        "X-Api-Key": settings.apollo_api_key,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    try:
        with httpx.Client(
            timeout=settings.enrichment_request_timeout_secs
        ) as cli:
            resp = cli.post(_APOLLO_PEOPLE_MATCH, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("apollo transport error for %s @ %s: %s", name, domain, exc)
        return None
    if resp.status_code >= 400:
        log.warning(
            "apollo http %d for %s @ %s: %s",
            resp.status_code,
            name,
            domain,
            resp.text[:200],
        )
        return None
    person = (resp.json() or {}).get("person") or {}
    email = person.get("email")
    # Apollo returns a placeholder when the email isn't actually revealed.
    if not email or "email_not_unlocked" in email or "@" not in email:
        return None
    # Apollo doesn't return a numeric confidence; treat a revealed,
    # domain-matched email as a solid hit. email_status refines it:
    # 'verified' is Apollo's own deliverability check.
    confidence = 0.85 if person.get("email_status") == "verified" else 0.7
    return EnrichmentResult(
        email=email,
        phone=None,
        title=person.get("title") or title_hint,
        linkedin_url=person.get("linkedin_url"),
        source="apollo",
        confidence=confidence,
    )


def _lookup_rocketreach(
    name: str, domain: str, title_hint: str | None
) -> EnrichmentResult | None:
    """RocketReach person/lookup as third-line fallback. Often resolves
    educator emails that Hunter and Apollo miss; per-credit pricing keeps
    cost bounded.

    Auth is via the `Api-Key` header. We pass name + current_employer
    (the org domain) to disambiguate. RocketReach returns a list of
    `emails` with `smtp_valid` grades; we pick the best deliverable one.
    Returns None on no-key, unsplittable name, error, or no email.
    """
    if not settings.rocketreach_api_key:
        log.debug("rocketreach: no key configured, skipping %s @ %s", name, domain)
        return None
    if split_name(name) is None:
        return None
    params = {"name": name, "current_employer": domain}
    headers = {"Api-Key": settings.rocketreach_api_key}
    try:
        with httpx.Client(
            timeout=settings.enrichment_request_timeout_secs
        ) as cli:
            resp = cli.get(_ROCKETREACH_LOOKUP, params=params, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("rocketreach transport error for %s @ %s: %s", name, domain, exc)
        return None
    if resp.status_code >= 400:
        log.warning(
            "rocketreach http %d for %s @ %s: %s",
            resp.status_code,
            name,
            domain,
            resp.text[:200],
        )
        return None
    data = resp.json() or {}
    email, confidence = _best_rocketreach_email(data.get("emails") or [], domain)
    if not email:
        # Fall back to the top-level current_work_email if present.
        email = data.get("current_work_email")
        confidence = 0.6
    if not email or "@" not in email:
        return None
    return EnrichmentResult(
        email=email,
        phone=None,
        title=data.get("current_title") or title_hint,
        linkedin_url=data.get("linkedin_url"),
        source="rocketreach",
        confidence=confidence,
    )


def _best_rocketreach_email(
    emails: list[dict], domain: str
) -> tuple[str | None, float]:
    """Pick the best email from RocketReach's `emails` list. Prefers an
    address on the target domain with an SMTP-valid grade, then any valid,
    then the first present. Returns (email, confidence)."""
    best: tuple[str | None, float] = (None, 0.0)
    for item in emails:
        addr = (item.get("email") or "").strip()
        if not addr or "@" not in addr:
            continue
        smtp_valid = (item.get("smtp_valid") or "").lower() == "valid"
        on_domain = addr.lower().endswith("@" + domain.lower())
        if on_domain and smtp_valid:
            return addr, 0.85
        score = 0.7 if smtp_valid else (0.65 if on_domain else 0.5)
        if score > best[1]:
            best = (addr, score)
    return best


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
