"""CRM Bridge tests.

The sync path (`sync_agencies` / `sync_agent_users`) takes an explicit `rows`
iterable so we can drive it with synthetic CRM rows without standing up a
fixture MySQL container. The dedupe path runs against the real local Postgres
(with trgm + functional indexes) — that's what actually matters because the
SQL we'd want to verify in a MySQL fixture is purely the SELECT shape, which
is exercised by an integration check in services/crm_bridge.py::connect_crm
when run end-to-end on prospect.

Per project memory (no-mocked-DB): integration tests must hit a real database.
The cache tables here ARE the real database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.models import Suppression, CRMAgenciesCache, CRMAgentUsersCache, User
from app.services.crm_bridge import (
    DedupeResult,
    extract_domain,
    lookup_contact_dedupe,
    lookup_organization_dedupe,
    sync_agencies,
    sync_agent_users,
)


# ---------- extract_domain ----------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://www.example.com/foo", "example.com"),
        ("http://Example.COM", "example.com"),
        ("alice@school.k12.tx.us", "school.k12.tx.us"),
        ("plain-domain.org", "plain-domain.org"),
        ("www.no-scheme.com/path?q=1", "no-scheme.com"),
        ("", None),
        (None, None),
        ("not a url", "not"),  # regex stops at whitespace — documents the behavior
    ],
)
def test_extract_domain(value, expected) -> None:
    assert extract_domain(value) == expected


# ---------- fixture: clean cache + seed ----------


@pytest.fixture()
def cache_seed():
    """Wipe + seed the CRM caches and suppressions with synthetic rows.

    Yields a dict with the inserted rows so tests can assert on crm_ids.
    Cleanup runs after so each test starts from a known baseline.
    """
    db = SessionLocal()
    try:
        # Make sure we don't trip on FKs from earlier crawl-test orgs that point
        # at crm_agency_ids. The dedupe queries don't reach into organizations,
        # so wiping just the caches is enough.
        db.execute(text("DELETE FROM crm_agencies_cache"))
        db.execute(text("DELETE FROM crm_agent_users_cache"))
        db.execute(text("DELETE FROM suppressions"))
        db.commit()

        # Seed three agencies covering all three status buckets + two agent users.
        # last_booking_at is what drives the customer/inactive/prospect bucket;
        # the sync derives status from it.
        recent = datetime.now(timezone.utc) - timedelta(days=180)   # ~6mo ago → customer
        stale = datetime.now(timezone.utc) - timedelta(days=900)    # ~30mo ago → inactive
        agencies = [
            {
                "AgCode": "ABC123",
                "AgName": "Phoenix Independent School District",
                "URL": "https://phoenixisd.example.org",
                "Email": None,
                "City": "Phoenix",
                "State": "AZ",
                "last_booking_at": recent,
            },
            {
                "AgCode": "XYZ999",
                "AgName": "Lincoln High School",
                "URL": "https://lhsmusic.example.com",
                "Email": None,
                "City": "Lincoln",
                "State": "NE",
                "last_booking_at": stale,
            },
            {
                "AgCode": "PRS555",
                "AgName": "Newville Prospect Academy",
                "URL": "https://newvilleprospect.example.net",
                "Email": None,
                "City": "Newville",
                "State": "OR",
                "last_booking_at": None,  # never booked → prospect
            },
        ]
        agent_users = [
            {
                "id": 1001,
                "agcode": "ABC123",
                "email": "Director@phoenixisd.example.org",
                "AltEmail": None,
            },
            {
                "id": 1002,
                "agcode": "XYZ999",
                "email": "bandlead@lhsmusic.example.com",
                "AltEmail": "alt-lead@lhsmusic.example.com",
            },
        ]
        sync_agencies(db, agencies)
        sync_agent_users(db, agent_users)

        # Need a user for the suppression's added_by FK.
        sup_user = User(
            email=f"sup-{uuid.uuid4()}@acme-travel.example",
            full_name="Suppression Owner",
            role="admin",
        )
        db.add(sup_user)
        db.flush()
        db.add_all(
            [
                Suppression(
                    type="email",
                    value="bad-actor@spammer.test",
                    value_lower="bad-actor@spammer.test",
                    reason="hard bounce",
                    added_by=sup_user.id,
                ),
                Suppression(
                    type="domain",
                    value="ScamSchool.test",
                    value_lower="scamschool.test",
                    reason="catch-all complaints",
                    added_by=sup_user.id,
                ),
            ]
        )
        db.commit()

        yield {
            "phoenix_isd": agencies[0],   # customer
            "lincoln_hs": agencies[1],    # inactive
            "newville": agencies[2],      # prospect
            "phoenix_user": agent_users[0],
            "lincoln_user": agent_users[1],
        }
    finally:
        db.close()


# ---------- sync_agencies / sync_agent_users ----------


def test_sync_agencies_upserts(cache_seed) -> None:
    db = SessionLocal()
    try:
        assert db.query(CRMAgenciesCache).count() == 3
        phoenix = db.get(CRMAgenciesCache, "ABC123")
        assert phoenix is not None
        assert phoenix.name == "Phoenix Independent School District"
        assert phoenix.domain == "phoenixisd.example.org"
        assert phoenix.state == "AZ"
        assert phoenix.status == "customer"

        lincoln = db.get(CRMAgenciesCache, "XYZ999")
        assert lincoln.status == "inactive"

        newville = db.get(CRMAgenciesCache, "PRS555")
        assert newville.status == "prospect"
        assert newville.last_booking_at is None

        # Re-running the sync with updated values must update in place.
        sync_agencies(
            db,
            [
                {
                    "AgCode": "ABC123",
                    "AgName": "Phoenix ISD (renamed)",
                    "URL": "https://newdomain.example.org",
                    "Email": None,
                    "City": "Phoenix",
                    "State": "AZ",
                    "last_booking_at": datetime.now(timezone.utc) - timedelta(days=30),
                }
            ],
        )
        # Session uses expire_on_commit=False (FastAPI-friendly), so a cached
        # ORM object won't reflect the UPSERT until we expire/refresh it.
        db.expire(phoenix)
        phoenix = db.get(CRMAgenciesCache, "ABC123")
        assert phoenix.name == "Phoenix ISD (renamed)"
        assert phoenix.domain == "newdomain.example.org"
        assert phoenix.status == "customer"
        assert db.query(CRMAgenciesCache).count() == 3  # no duplicate row
    finally:
        db.close()


def test_sync_agent_users_lowercases_email(cache_seed) -> None:
    db = SessionLocal()
    try:
        u = db.get(CRMAgentUsersCache, 1001)
        assert u is not None
        assert u.email == "director@phoenixisd.example.org"  # lowercased
        assert u.agency_id == "ABC123"
    finally:
        db.close()


# ---------- dedupe: contact ----------


def test_contact_dedupe_exact_email_blocks(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="DIRECTOR@phoenixisd.example.org")
    finally:
        db.close()
    assert isinstance(r, DedupeResult)
    assert r.status == "exact"
    assert r.blocks_export is True
    assert r.crm_agent_user_id == 1001


def test_contact_dedupe_exact_via_alt_email(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="alt-lead@lhsmusic.example.com")
    finally:
        db.close()
    assert r.status == "exact"
    assert r.crm_agent_user_id == 1002


def test_contact_dedupe_suppressed_email_blocks(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="bad-actor@spammer.test")
    finally:
        db.close()
    assert r.status == "suppressed"
    assert r.blocks_export is True
    assert "hard bounce" in r.reason


def test_contact_dedupe_suppressed_domain_blocks(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="anyone@scamschool.test")
    finally:
        db.close()
    assert r.status == "suppressed"
    assert r.blocks_export is True


def test_contact_dedupe_domain_match_blocks_when_customer(cache_seed) -> None:
    """New email at a domain we know belongs to an active CRM customer."""
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="newperson@phoenixisd.example.org")
    finally:
        db.close()
    assert r.status == "domain_match"
    assert r.blocks_export is True
    assert r.crm_agency_id == "ABC123"


def test_contact_dedupe_domain_match_flags_when_inactive(cache_seed) -> None:
    """Same domain pattern but the CRM agency is inactive (no bookings
    in 2+ years) → flag only."""
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="newperson@lhsmusic.example.com")
    finally:
        db.close()
    assert r.status == "domain_match"
    assert r.blocks_export is False  # inactive → flag, don't block
    assert r.crm_agency_id == "XYZ999"
    assert "inactive" in r.reason


def test_contact_dedupe_domain_match_blocks_when_prospect(cache_seed) -> None:
    """Prospect domain: already in our pipeline as a lead, so outbound
    would be a duplicate touch — block per the policy."""
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="newperson@newvilleprospect.example.net")
    finally:
        db.close()
    assert r.status == "domain_match"
    assert r.blocks_export is True
    assert r.crm_agency_id == "PRS555"
    assert "prospect" in r.reason


def test_contact_dedupe_new_when_nothing_matches(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_contact_dedupe(db, email="brand-new@somewhere.example.net")
    finally:
        db.close()
    assert r.status == "new"
    assert r.blocks_export is False


# ---------- dedupe: organization (trgm tiers) ----------


def test_org_dedupe_domain_match_short_circuits(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_organization_dedupe(db, name="Some Different Name", domain="phoenixisd.example.org")
    finally:
        db.close()
    assert r.status == "domain_match"
    assert r.crm_agency_id == "ABC123"


def test_org_dedupe_possible_match_with_state(cache_seed) -> None:
    """High name similarity + state agreement → possible_match (flag only)."""
    db = SessionLocal()
    try:
        r = lookup_organization_dedupe(db, name="Phoenix Independent School Dist", state="AZ")
    finally:
        db.close()
    assert r.status == "possible_match"
    assert r.blocks_export is False
    assert r.crm_agency_id == "ABC123"
    assert r.match_score is not None and r.match_score >= 0.8


def test_org_dedupe_weak_match_without_geo(cache_seed) -> None:
    """High-ish similarity but no state/domain agreement → weak_match."""
    db = SessionLocal()
    try:
        # Spec §10.3: trgm > 0.7 alone (no geo) → weak_match.
        # Use a near-duplicate name with a non-matching state.
        r = lookup_organization_dedupe(db, name="Phoenix Independent School District", state="CA")
    finally:
        db.close()
    # Either weak_match (similarity high enough but no state agreement) or
    # possible_match if state happens to coincide — for this fixture data
    # ("AZ" vs queried "CA") it must be weak_match.
    assert r.status == "weak_match"
    assert r.blocks_export is False


def test_org_dedupe_collision_blocked_by_geo_requirement(cache_seed) -> None:
    """Lincoln High School is in 40+ districts — spec §10.3 explicitly calls
    this out. Without state agreement we should NOT call it possible_match."""
    db = SessionLocal()
    try:
        r = lookup_organization_dedupe(db, name="Lincoln High School", state="CA")
    finally:
        db.close()
    # Exact-string trgm sim = 1.0 but state doesn't agree (NE vs CA) →
    # downgraded to weak_match per spec.
    assert r.status == "weak_match"
    assert r.blocks_export is False


def test_org_dedupe_new_when_below_threshold(cache_seed) -> None:
    db = SessionLocal()
    try:
        r = lookup_organization_dedupe(db, name="Completely Unrelated Robotics Camp")
    finally:
        db.close()
    assert r.status == "new"
    assert r.blocks_export is False
