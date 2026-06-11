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
import re
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from app.config import settings
from app.services.email_pattern import split_name

log = logging.getLogger(__name__)

_HUNTER_EMAIL_FINDER = "https://api.hunter.io/v2/email-finder"
_APOLLO_PEOPLE_MATCH = "https://api.apollo.io/api/v1/people/match"
_ROCKETREACH_LOOKUP = "https://api.rocketreach.co/api/v2/person/lookup"
_ROCKETREACH_CHECK_STATUS = "https://api.rocketreach.co/api/v2/person/checkStatus"
_PERPLEXITY_AGENT_ENDPOINT = "https://api.perplexity.ai/v1/responses"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LINKEDIN_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%-]+")

# RocketReach lookups are async — a fresh lookup returns status="progress"
# with only the cached emails subset. Poll checkStatus until "complete"
# (or this many tries) to get the fully-enriched payload.
_ROCKETREACH_MAX_POLLS = 4
_ROCKETREACH_POLL_DELAY_SECS = 2.0


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
    (the org domain) to disambiguate. RocketReach's lookup is async: a
    fresh request returns `status="progress"` with only cached emails;
    we poll checkStatus until "complete" then re-fetch so the `emails`
    array contains the full enriched set. Returns None on no-key,
    unsplittable name, error, or no email.
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
            data = _await_rocketreach_complete(cli, data, headers)
    except httpx.HTTPError as exc:
        log.warning("rocketreach transport error for %s @ %s: %s", name, domain, exc)
        return None
    email, confidence = _best_rocketreach_email(data.get("emails") or [], domain)
    if not email:
        # RR's curated "best professional email" pick — on-domain when
        # present, beats the noisier current_work_email fallback.
        email = data.get("recommended_professional_email")
        if email:
            confidence = 0.7 if email.lower().endswith("@" + domain.lower()) else 0.6
    if not email:
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


def _await_rocketreach_complete(
    cli: httpx.Client, data: dict, headers: dict
) -> dict:
    """Poll checkStatus + re-lookup until the profile reports `complete`,
    or the poll budget is exhausted. Returns whatever payload we have at
    the end — partial cached emails are still better than nothing."""
    if data.get("status") != "progress":
        return data
    rr_id = data.get("id")
    if not rr_id:
        return data
    for _ in range(_ROCKETREACH_MAX_POLLS):
        time.sleep(_ROCKETREACH_POLL_DELAY_SECS)
        try:
            status_resp = cli.get(
                _ROCKETREACH_CHECK_STATUS,
                params={"ids": str(rr_id)},
                headers=headers,
            )
        except httpx.HTTPError:
            return data
        if status_resp.status_code >= 400:
            return data
        body = status_resp.json()
        entry = body[0] if isinstance(body, list) and body else body
        if (entry or {}).get("status") != "complete":
            continue
        try:
            final = cli.get(
                _ROCKETREACH_LOOKUP,
                params={"id": str(rr_id)},
                headers=headers,
            )
        except httpx.HTTPError:
            return data
        if final.status_code < 400:
            return final.json() or data
        return data
    return data


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


def _parse_agent_text(data: dict) -> tuple[str, float | None]:
    """→ (answer_text, total_cost_usd) from a Perplexity /v1/responses payload."""
    parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text"):
                    parts.append(c["text"])
    cost = ((data.get("usage") or {}).get("cost") or {}).get("total_cost")
    return "\n".join(parts), cost


def _lookup_perplexity_agent(
    name: str, domain: str, title_hint: str | None
) -> EnrichmentResult | None:
    """Last-resort vendor: Perplexity's Agent API runs a server-side web-research
    loop and returns a cited answer we mine for a same-domain email (plus title /
    LinkedIn if present). Prose-extracted, so confidence is deliberately below
    MIN_CONFIDENT — the structured vendors above always win. Reports exact per-call
    cost (usage.cost). Returns None unless it found an email (the chain wants data)."""
    if not settings.enrichment_perplexity_api_key:
        log.debug("perplexity_agent: no key configured, skipping %s @ %s", name, domain)
        return None
    prompt = (
        f"Find the professional work contact details for {name}"
        + (f" ({title_hint})" if title_hint else "")
        + f" at the organization whose website is {domain}. From public sources, give their "
        f"work email address (prefer one on the {domain} domain), job title, and LinkedIn "
        "profile URL if available. If you cannot find a verified email, say so explicitly — "
        "do not guess or construct an email from a pattern."
    )
    body = {
        "model": settings.enrichment_perplexity_model,
        "input": prompt,
        "tools": [{"type": "web_search"}],
    }
    headers = {
        "Authorization": f"Bearer {settings.enrichment_perplexity_api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=settings.enrichment_perplexity_timeout_secs) as cli:
            resp = cli.post(_PERPLEXITY_AGENT_ENDPOINT, headers=headers, json=body)
    except httpx.HTTPError as exc:
        log.warning("perplexity_agent transport error for %s @ %s: %s", name, domain, exc)
        return None
    if resp.status_code >= 400:
        log.warning("perplexity_agent http %d for %s @ %s: %s",
                    resp.status_code, name, domain, resp.text[:200])
        return None

    text, cost = _parse_agent_text(resp.json())
    log.info("perplexity_agent cost $%.4f for %s @ %s", cost or 0.0, name, domain)
    if not text:
        return None
    bare = domain.split("/")[0].lstrip("www.").lower()
    emails = _EMAIL_RE.findall(text)
    # Prefer a same-domain address; else the first plausible one.
    email = next((e for e in emails if e.lower().endswith("@" + bare)), emails[0] if emails else None)
    if not email:
        return None
    linkedin = (_LINKEDIN_RE.search(text) or [None])
    linkedin_url = linkedin.group(0) if hasattr(linkedin, "group") else None
    # On-domain email = stronger signal than a generic public one, but still prose-derived.
    confidence = 0.6 if email.lower().endswith("@" + bare) else 0.5
    return EnrichmentResult(
        email=email,
        phone=None,
        title=title_hint,
        linkedin_url=linkedin_url,
        source="perplexity_agent",
        confidence=confidence,
    )


# Chain order matters — cheapest, most accurate vendor first. Stop on
# first confident hit (confidence >= MIN_CONFIDENT). The Perplexity Agent
# vendor is last: pricier, slower, and prose-derived (sub-threshold confidence),
# so it only contributes when the structured vendors found nothing.
_PROVIDER_CHAIN: tuple[ProviderLookup, ...] = (
    _lookup_hunter,
    _lookup_apollo,
    _lookup_rocketreach,
    _lookup_perplexity_agent,
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
