"""CRM Bridge — read-only sync + match-tier dedupe (spec §10).

Two responsibilities:

1. **Sync** (`sync_agencies`, `sync_agent_users`) pulls rows from the live
   CRM DB (read-only via the `prospectforge_readonly` MySQL grant) into
   our local Postgres cache. Nightly via cron / RQ schedule. Never writes
   to CRM.

2. **Dedupe** (`lookup_contact_dedupe`, `lookup_organization_dedupe`,
   `check_suppression`) classifies an extracted contact/org against the
   cache + local suppressions and returns a DedupeResult with status +
   human-readable reason. Tier logic per spec §10.3:

   | Tier | Trigger                                            | Status          |
   |------|----------------------------------------------------|-----------------|
   | 0    | local suppression on email or domain               | suppressed      |
   | 1    | email = tblAgentUsers.email or AltEmail (case-ins) | exact           |
   | 2    | email domain = tblAgency.URL domain                | domain_match    |
   | 3    | trgm(name) > 0.8 AND (state OR domain match)       | possible_match  |
   | 4    | trgm(name) > 0.7 alone                             | weak_match      |
   | 5    | none                                               | new             |

   Suppression and exact email match are blocking. domain_match blocks if
   the matched CRM agency status reads as a customer, otherwise flags.
   trgm tiers only flag — they never block.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import pymysql
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Suppression,
    CRMAgenciesCache,
    CRMAgentUsersCache,
)

log = logging.getLogger(__name__)


# ---------- value extraction helpers ----------


_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([^/:?#\s]+)", re.IGNORECASE)


def extract_domain(value: str | None) -> str | None:
    """Pull the registrable-host portion out of a URL or email. Lowercased.

    Trims www. but otherwise preserves the host as CRM stored it — we
    don't have a PSL handy and getting "registrable domain" perfect is a
    rabbit hole. For dedupe purposes, comparing `theateragency.com` to
    `theateragency.com` is what we need; subdomain differences are rare in
    school/agency URLs and worth treating as a domain_match anyway.
    """
    if not value:
        return None
    value = value.strip().lower()
    if "@" in value:
        return value.split("@", 1)[1] or None
    m = _DOMAIN_RE.match(value)
    if not m:
        return None
    host = m.group(1)
    return host or None


_INACTIVE_THRESHOLD_DAYS = 730  # 24 months — spec via Jacob 2026-05-12


def _classify_blocking(status: str | None) -> tuple[bool, str]:
    """Map a cache status string to (blocks_export, descriptor).

    Per Jacob 2026-05-12: customer/prospect/inactive are derived from booking
    recency, computed at sync time and stored verbatim in the cache.

    Blocking policy:
        customer → block (active relationship; sales has the seat)
        prospect → block (already in our pipeline as a lead; would double-touch)
        inactive → flag only (cold for 2+ years; reviewer can re-engage)
    """
    if status == "customer":
        return True, "customer"
    if status == "prospect":
        return True, "prospect (in CRM pipeline, no bookings yet)"
    if status == "inactive":
        return False, "inactive (no bookings in 24+ months)"
    # Unknown / legacy value — treat conservatively to avoid duplicate outreach.
    return True, f"unclassified ({status!r})"


# ---------- result type ----------


@dataclass
class DedupeResult:
    """Outcome of a dedup lookup. `reason` is shown verbatim in the review UI."""

    status: str
    # exact | domain_match | possible_match | weak_match | suppressed | new
    reason: str
    blocks_export: bool
    crm_agency_id: str | None = None
    crm_agent_user_id: int | None = None
    match_score: float | None = None


# ---------- sync ----------


def connect_crm() -> pymysql.connections.Connection:
    """Open a read-only pymysql connection. Caller owns lifetime (use `with`)."""
    return pymysql.connect(
        host=settings.crm_db_host,
        port=settings.crm_db_port,
        user=settings.crm_db_user,
        password=settings.crm_db_password,
        database=settings.crm_db_name,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=60,
        autocommit=True,
    )


def _derive_status(last_booking_at: datetime | None, now: datetime) -> str:
    """Map a last_booking_at timestamp to one of {customer, inactive, prospect}.

    customer: booked within INACTIVE_THRESHOLD_DAYS (24 months)
    inactive: had bookings but the most recent is older than the threshold
    prospect: never had a non-cancelled, group-bearing booking
    """
    if last_booking_at is None:
        return "prospect"
    age_days = (now - last_booking_at).total_seconds() / 86400.0
    return "customer" if age_days <= _INACTIVE_THRESHOLD_DAYS else "inactive"


def sync_agencies(
    db: Session,
    rows: Iterable[dict] | None = None,
    *,
    crm_conn: pymysql.connections.Connection | None = None,
) -> int:
    """Upsert tblAgency rows into the local cache.

    Each row is expected to carry `last_booking_at` already computed (the
    fetcher joins tblBooking to produce it). For tests/fixtures, pass rows
    directly; the fixture is responsible for populating last_booking_at.

    Returns count upserted.
    """
    if rows is None:
        rows = _fetch_agencies(crm_conn)
    upserted = 0
    now = datetime.now(timezone.utc)
    for r in rows:
        domain = extract_domain(r.get("URL")) or extract_domain(r.get("Email"))
        last_booking = r.get("last_booking_at")
        if last_booking is not None and last_booking.tzinfo is None:
            # MySQL DATETIME returns naive; assume UTC for arithmetic
            # consistency with Postgres timestamptz columns.
            last_booking = last_booking.replace(tzinfo=timezone.utc)
        values = {
            "crm_id": str(r["AgCode"]),
            "name": r.get("AgName") or "",
            "domain": domain,
            "city": r.get("City"),
            "state": (r.get("State") or "").strip() or None,
            "status": _derive_status(last_booking, now),
            "last_booking_at": last_booking,
            "last_synced_at": now,
        }
        stmt = insert(CRMAgenciesCache).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CRMAgenciesCache.crm_id],
            set_={k: stmt.excluded[k] for k in values if k != "crm_id"},
        )
        db.execute(stmt)
        upserted += 1
    db.commit()
    return upserted


def sync_agent_users(
    db: Session,
    rows: Iterable[dict] | None = None,
    *,
    crm_conn: pymysql.connections.Connection | None = None,
) -> int:
    """Upsert tblAgentUsers rows into the local cache."""
    if rows is None:
        rows = _fetch_agent_users(crm_conn)
    upserted = 0
    now = datetime.now(timezone.utc)
    for r in rows:
        values = {
            "crm_id": int(r["id"]),
            "agency_id": str(r["agcode"]) if r.get("agcode") else None,
            "email": (r.get("email") or "").lower() or None,
            "alt_email": (r.get("AltEmail") or "").lower() or None,
            "last_synced_at": now,
        }
        stmt = insert(CRMAgentUsersCache).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CRMAgentUsersCache.crm_id],
            set_={k: stmt.excluded[k] for k in values if k != "crm_id"},
        )
        db.execute(stmt)
        upserted += 1
    db.commit()
    return upserted


def _fetch_agencies(conn: pymysql.connections.Connection | None) -> Iterable[dict]:
    """Pull active agencies + their most recent non-cancelled, group-bearing
    booking date. The LEFT JOIN ensures agencies with no bookings come back
    (last_booking_at=NULL → prospect)."""
    owned = conn is None
    if owned:
        conn = connect_crm()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.AgCode, a.AgName, a.URL, a.Email, a.City, a.State, "
                "       MAX(b.DateBooked) AS last_booking_at "
                "FROM tblAgency a "
                "LEFT JOIN tblBooking b "
                "       ON b.AgCode = a.AgCode "
                "      AND b.GroupID IS NOT NULL "
                "      AND (b.Status IS NULL OR b.Status <> 'CX') "
                "WHERE a.Active = 1 "
                "GROUP BY a.AgCode, a.AgName, a.URL, a.Email, a.City, a.State"
            )
            return cur.fetchall()
    finally:
        if owned:
            conn.close()


def _fetch_agent_users(conn: pymysql.connections.Connection | None) -> Iterable[dict]:
    owned = conn is None
    if owned:
        conn = connect_crm()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, agcode, email, AltEmail "
                "FROM tblAgentUsers WHERE Status = 1 AND OptOutDate IS NULL"
            )
            return cur.fetchall()
    finally:
        if owned:
            conn.close()


# ---------- dedupe lookups ----------


def check_suppression(db: Session, value: str, sup_type: str) -> Suppression | None:
    """Look up an exact match in the local suppressions table."""
    return (
        db.query(Suppression)
        .filter(Suppression.type == sup_type, Suppression.value_lower == value.lower())
        .first()
    )


def lookup_contact_dedupe(
    db: Session,
    *,
    email: str | None,
    org_name: str | None = None,
    state: str | None = None,
) -> DedupeResult:
    """Run a contact through the full tier ladder. Email-driven."""

    # ---- Tier 0: suppression ----
    if email:
        sup = check_suppression(db, email, "email")
        if sup:
            return DedupeResult(
                status="suppressed",
                reason=f"email suppressed: {sup.reason}",
                blocks_export=True,
            )
        domain = extract_domain(email)
        if domain:
            sup = check_suppression(db, domain, "domain")
            if sup:
                return DedupeResult(
                    status="suppressed",
                    reason=f"domain {domain} suppressed: {sup.reason}",
                    blocks_export=True,
                )

    # ---- Tier 1: exact email match against tblAgentUsers ----
    if email:
        match = db.execute(
            text(
                "SELECT crm_id, agency_id "
                "FROM crm_agent_users_cache "
                "WHERE lower(email) = :e OR lower(alt_email) = :e "
                "LIMIT 1"
            ),
            {"e": email.lower()},
        ).first()
        if match:
            return DedupeResult(
                status="exact",
                reason=f"email matches CRM agent user (id={match.crm_id})",
                blocks_export=True,
                crm_agent_user_id=match.crm_id,
                crm_agency_id=match.agency_id,
            )

    # ---- Tier 2: domain match against tblAgency.URL ----
    if email:
        email_domain = extract_domain(email)
        if email_domain:
            agency_match = db.execute(
                text(
                    "SELECT crm_id, name, status "
                    "FROM crm_agencies_cache "
                    "WHERE lower(domain) = :d "
                    "LIMIT 1"
                ),
                {"d": email_domain},
            ).first()
            if agency_match:
                blocks, descriptor = _classify_blocking(agency_match.status)
                return DedupeResult(
                    status="domain_match",
                    reason=(
                        f"email domain matches CRM agency {agency_match.name!r} "
                        f"[{descriptor}] — {'block' if blocks else 'flag'}"
                    ),
                    blocks_export=blocks,
                    crm_agency_id=agency_match.crm_id,
                )

    # ---- Tier 3 & 4: trgm name match ----
    if org_name:
        org_result = lookup_organization_dedupe(db, name=org_name, state=state, domain=None)
        if org_result.status != "new":
            return DedupeResult(
                status=org_result.status,
                reason=f"org-level: {org_result.reason}",
                blocks_export=org_result.blocks_export,
                crm_agency_id=org_result.crm_agency_id,
                match_score=org_result.match_score,
            )

    return DedupeResult(status="new", reason="no matches", blocks_export=False)


def lookup_organization_dedupe(
    db: Session,
    *,
    name: str,
    state: str | None = None,
    domain: str | None = None,
) -> DedupeResult:
    """Dedupe at the org level. Uses pg_trgm similarity on the agency name."""
    if not name or not name.strip():
        return DedupeResult(status="new", reason="empty name", blocks_export=False)

    # Tier 2 short-circuit on domain
    if domain:
        agency = db.execute(
            text(
                "SELECT crm_id, name, state, status "
                "FROM crm_agencies_cache "
                "WHERE lower(domain) = :d "
                "LIMIT 1"
            ),
            {"d": domain.lower()},
        ).first()
        if agency:
            blocks, descriptor = _classify_blocking(agency.status)
            return DedupeResult(
                status="domain_match",
                reason=f"domain matches CRM agency {agency.name!r} [{descriptor}]",
                blocks_export=blocks,
                crm_agency_id=agency.crm_id,
            )

    # Tier 3/4: trgm. We pull the best candidate above 0.7 then decide which
    # tier based on threshold + state/domain agreement.
    candidate = db.execute(
        text(
            "SELECT crm_id, name, state, domain, status, "
            "       similarity(name, :n) AS sim "
            "FROM crm_agencies_cache "
            "WHERE name % :n "
            "ORDER BY sim DESC "
            "LIMIT 1"
        ),
        {"n": name},
    ).first()
    if not candidate or candidate.sim is None:
        return DedupeResult(status="new", reason="no name match above trgm threshold", blocks_export=False)

    state_match = bool(state and candidate.state and state.upper() == candidate.state.upper())
    domain_match = bool(domain and candidate.domain and domain.lower() == candidate.domain.lower())

    if candidate.sim >= 0.8 and (state_match or domain_match):
        return DedupeResult(
            status="possible_match",
            reason=(
                f"name ~ CRM {candidate.name!r} (trgm {candidate.sim:.2f}) "
                f"with {'state' if state_match else 'domain'} match"
            ),
            blocks_export=False,  # surfaced for review, never auto-blocks
            crm_agency_id=candidate.crm_id,
            match_score=float(candidate.sim),
        )
    if candidate.sim >= 0.7:
        return DedupeResult(
            status="weak_match",
            reason=(
                f"name ~ CRM {candidate.name!r} (trgm {candidate.sim:.2f}) "
                "without state/domain agreement"
            ),
            blocks_export=False,
            crm_agency_id=candidate.crm_id,
            match_score=float(candidate.sim),
        )

    return DedupeResult(status="new", reason="no high-confidence name match", blocks_export=False)
