"""Enrichment tests — minimal v1 (website backfill only)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db import SessionLocal
from app.models import Contact, Organization, ProspectRun, Source, User
from app.services.enrichment import (
    backfill_organization_domains,
    normalize_organization_website,
)


@pytest.fixture()
def fixture_org_with_contacts():
    db = SessionLocal()
    try:
        user = User(
            email=f"enr-{uuid.uuid4()}@acme-travel.example",
            full_name="Enr",
            role="admin",
        )
        db.add(user)
        db.flush()
        source = Source(name="x", url="https://example.com/", created_by=user.id)
        db.add(source)
        db.flush()
        run = ProspectRun(
            source_id=source.id,
            created_by=user.id,
            max_cost_cents=500,
            budget_approved_by=user.id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        org = Organization(run_id=run.id, name="Phoenix ISD")  # no domain
        db.add(org)
        db.flush()
        db.add_all(
            [
                Contact(
                    run_id=run.id,
                    organization_id=org.id,
                    name="A",
                    email="a@phoenixisd.example.org",
                    evidence_excerpt="A teaches band at Phoenix ISD's high school",
                    evidence_verified="verified",
                ),
                Contact(
                    run_id=run.id,
                    organization_id=org.id,
                    name="B",
                    email="b@phoenixisd.example.org",
                    evidence_excerpt="B teaches choir at Phoenix ISD's high school",
                    evidence_verified="verified",
                ),
            ]
        )
        db.commit()
        ids = {"run_id": run.id, "org_id": org.id}
    finally:
        db.close()
    return ids


def test_backfill_uses_majority_email_domain(fixture_org_with_contacts) -> None:
    db = SessionLocal()
    try:
        org = db.get(Organization, fixture_org_with_contacts["org_id"])
        assert org.domain is None
        backfill_organization_domains(db, organizations=[org])
        db.refresh(org)
        assert org.domain == "phoenixisd.example.org"
        assert org.website == "https://phoenixisd.example.org"
    finally:
        db.close()


def test_normalize_adds_scheme(fixture_org_with_contacts) -> None:
    db = SessionLocal()
    try:
        org = db.get(Organization, fixture_org_with_contacts["org_id"])
        org.website = "phoenixisd.example.org"
        db.commit()
        normalize_organization_website(db, organizations=[org])
        db.refresh(org)
        assert org.website == "https://phoenixisd.example.org"
    finally:
        db.close()


def test_backfill_skips_when_no_emails() -> None:
    db = SessionLocal()
    try:
        user = User(
            email=f"enr2-{uuid.uuid4()}@acme-travel.example",
            full_name="Enr",
            role="admin",
        )
        db.add(user)
        db.flush()
        source = Source(name="x", url="https://example.com/", created_by=user.id)
        db.add(source)
        db.flush()
        run = ProspectRun(
            source_id=source.id,
            created_by=user.id,
            max_cost_cents=100,
            budget_approved_by=user.id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        org = Organization(run_id=run.id, name="Empty Org")
        db.add(org)
        db.commit()
        backfilled = backfill_organization_domains(db, organizations=[org])
        assert backfilled == 0
        db.refresh(org)
        assert org.domain is None
    finally:
        db.close()
