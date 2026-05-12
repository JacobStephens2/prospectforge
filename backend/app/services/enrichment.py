"""Enrichment — spec §8 in its MVP shape.

What's in scope for v1 per spec §8.1:
  * Organization website backfill — if no domain, infer from contact emails

What's explicitly deferred to v1.1:
  * Hunter API waterfall
  * NeverBounce (gated only if Riley's sender requires it for first campaign)
  * Pattern-guess generation (requires ≥2 verified emails at same domain
    per §8.2 — wait for pilot data before turning on)

Single pure pass over a run's organizations after extraction.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Iterable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import Contact, Organization

log = logging.getLogger(__name__)


def _email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[1].strip().lower() or None


def _root_domain(host: str) -> str:
    """Drop www. prefix. Subdomain.example.org stays as-is — we don't have
    a Public Suffix List handy and getting "registrable domain" perfect is
    a rabbit hole. For dedupe + display purposes this is enough."""
    return host[4:] if host.startswith("www.") else host


def backfill_organization_domains(
    db: Session,
    *,
    organizations: Iterable[Organization],
) -> int:
    """For each org with no domain, infer from its contacts' email domains
    (majority vote). Returns count backfilled. Commits at the end."""
    backfilled = 0
    for org in organizations:
        if org.domain:
            continue
        contacts = db.query(Contact).filter(Contact.organization_id == org.id).all()
        counter: Counter[str] = Counter()
        for c in contacts:
            d = _email_domain(c.email) or _email_domain(c.alt_email)
            if d:
                counter[_root_domain(d)] += 1
        if not counter:
            continue
        winner, _ = counter.most_common(1)[0]
        org.domain = winner
        if not org.website:
            org.website = f"https://{winner}"
        backfilled += 1
    if backfilled:
        db.commit()
    return backfilled


def normalize_organization_website(
    db: Session,
    *,
    organizations: Iterable[Organization],
) -> int:
    """If website is set but lacks a scheme, prefix https://. Same idea for
    domain → website mirroring (the bridge dedupe path uses domain;
    the export uses website)."""
    fixed = 0
    for org in organizations:
        if org.website and not urlparse(org.website).scheme:
            org.website = f"https://{org.website.lstrip('/')}"
            fixed += 1
        if org.domain and not org.website:
            org.website = f"https://{org.domain}"
            fixed += 1
    if fixed:
        db.commit()
    return fixed
