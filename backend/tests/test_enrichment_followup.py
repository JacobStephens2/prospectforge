"""Post-crawl enrichment follow-up: pattern inference + vendor + validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db import SessionLocal
from app.models import Contact, Organization, ProspectRun, Source, User
from app.services import enrichment_followup
from app.services.email_validate import ValidationStatus


@pytest.fixture(autouse=True)
def _no_real_vendor_calls(monkeypatch):
    """Keep this module hermetic: never let the vendor waterfall make a real
    network call. With live HUNTER/APOLLO/ROCKETREACH keys in the environment
    (prod container), lookup_via_api would otherwise issue billed API requests
    and return non-deterministic results. Force 'no vendor hit' by default;
    vendor behavior itself is covered in test_enrichment_external.py."""
    monkeypatch.setattr(
        enrichment_followup, "lookup_via_api", lambda *a, **k: None
    )


def _setup(db, *, target_org_domain=None, samples=None, target_name="Anna Kowal-Byrne"):
    """Create a user/source, a SOURCE run holding the target contact, and a
    separate ENRICHMENT run (mirrors production: enrichment runs always
    target a contact from an earlier run) + sample contacts the enrichment
    crawl 'found' with emails. Returns (enrichment_run_id, target_id)."""
    u = User(email=f"fu-{uuid.uuid4()}@acme-travel.example", full_name="FU", role="admin")
    db.add(u)
    db.flush()
    s = Source(name="fu src", url="https://example.com/", created_by=u.id, active=False)
    db.add(s)
    db.flush()
    source_run = ProspectRun(
        source_id=s.id,
        created_by=u.id,
        max_cost_cents=50,
        budget_approved_by=u.id,
        budget_approved_at=datetime.now(timezone.utc),
    )
    enrich_run = ProspectRun(
        source_id=s.id,
        created_by=u.id,
        max_cost_cents=50,
        budget_approved_by=u.id,
        budget_approved_at=datetime.now(timezone.utc),
    )
    db.add_all([source_run, enrich_run])
    db.flush()
    org = Organization(
        run_id=source_run.id, name="Hillcrest Academy", domain=target_org_domain
    )
    db.add(org)
    db.flush()
    target = Contact(
        run_id=source_run.id,
        organization_id=org.id,
        name=target_name,
        evidence_excerpt="x" * 30,
    )
    db.add(target)
    db.flush()
    # Sample contacts found by the enrichment crawl (other staff, with emails).
    for name, email in samples or []:
        db.add(
            Contact(
                run_id=enrich_run.id,
                organization_id=org.id,
                name=name,
                email=email,
                evidence_excerpt="x" * 30,
            )
        )
    enrich_run.enrichment_for_contact_id = target.id
    db.commit()
    return enrich_run.id, target.id


def test_pattern_inference_adds_candidate(monkeypatch):
    # Validator off → UNKNOWN → labeled pattern_guess (still added).
    monkeypatch.setattr(
        enrichment_followup, "validate_email", lambda e: ValidationStatus.UNKNOWN
    )
    db = SessionLocal()
    try:
        run_id, target_id = _setup(
            db,
            target_org_domain="hillcrestacademy.example",
            samples=[
                ("John Doe", "jdoe@hillcrestacademy.example"),
                ("Jane Smith", "jsmith@hillcrestacademy.example"),
                ("Michelle Halvorsen", "mhalvorsen@hillcrestacademy.example"),
            ],
        )
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "candidate_added"
        # flast pattern → akowal-byrne@hillcrestacademy.example
        assert result["candidate"] == "akowal-byrne@hillcrestacademy.example"
        assert result["confidence"] == "pattern_guess"

        # A suggestion contact now exists under the target.
        sugg = (
            db.query(Contact)
            .filter(Contact.parent_contact_id == target_id)
            .all()
        )
        assert any(c.email == "akowal-byrne@hillcrestacademy.example" for c in sugg)
    finally:
        db.close()


def test_valid_validation_marks_verified(monkeypatch):
    monkeypatch.setattr(
        enrichment_followup, "validate_email", lambda e: ValidationStatus.VALID
    )
    db = SessionLocal()
    try:
        run_id, target_id = _setup(
            db,
            target_org_domain="hillcrestacademy.example",
            samples=[
                ("John Doe", "jdoe@hillcrestacademy.example"),
                ("Jane Smith", "jsmith@hillcrestacademy.example"),
                ("Bob Jones", "bjones@hillcrestacademy.example"),
            ],
        )
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "candidate_added"
        assert result["confidence"] == "verified"
        # Validator-confirmed email is auto-applied to the contact itself.
        assert result["auto_applied"] is True
        target = db.get(Contact, target_id)
        db.refresh(target)
        assert target.email == result["candidate"]
        assert target.email_confidence == "verified"
        assert target.score_has_direct_email is True
    finally:
        db.close()


def test_invalid_validation_drops_candidate(monkeypatch):
    monkeypatch.setattr(
        enrichment_followup, "validate_email", lambda e: ValidationStatus.INVALID
    )
    db = SessionLocal()
    try:
        run_id, target_id = _setup(
            db,
            target_org_domain="hillcrestacademy.example",
            samples=[
                ("John Doe", "jdoe@hillcrestacademy.example"),
                ("Jane Smith", "jsmith@hillcrestacademy.example"),
                ("Bob Jones", "bjones@hillcrestacademy.example"),
            ],
        )
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "candidate_invalid"
        # No suggestion persisted.
        sugg = (
            db.query(Contact)
            .filter(Contact.parent_contact_id == target_id)
            .all()
        )
        assert sugg == []
    finally:
        db.close()


def test_catchall_marks_unverifiable(monkeypatch):
    monkeypatch.setattr(
        enrichment_followup, "validate_email", lambda e: ValidationStatus.CATCHALL
    )
    db = SessionLocal()
    try:
        run_id, _ = _setup(
            db,
            target_org_domain="hillcrestacademy.example",
            samples=[
                ("John Doe", "jdoe@hillcrestacademy.example"),
                ("Jane Smith", "jsmith@hillcrestacademy.example"),
                ("Bob Jones", "bjones@hillcrestacademy.example"),
            ],
        )
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["confidence"] == "unverifiable_catchall"
        # Catch-all is NOT auto-applied — stamping an unconfirmed guess onto
        # the contact is the risk the validation gate exists to prevent. It
        # stays a suggestion for a human call.
        assert result["auto_applied"] is False
        run = db.get(ProspectRun, run_id)
        target = db.get(Contact, run.enrichment_for_contact_id)
        db.refresh(target)
        assert target.email is None
    finally:
        db.close()


def test_crawl_found_self_email_is_auto_applied(monkeypatch):
    """When the crawl itself extracted the target's own email (a same-name
    suggestion), it's literal page text — apply it to the contact directly."""
    db = SessionLocal()
    try:
        run_id, target_id = _setup(
            db, target_org_domain="hillcrestacademy.example", samples=[]
        )
        # Simulate the crawl having extracted the target person WITH email.
        db.add(
            Contact(
                run_id=run_id,
                parent_contact_id=target_id,
                name="Anna Kowal-Byrne",
                email="akowal-byrne@hillcrestacademy.example",
                evidence_excerpt="x" * 30,
                review_status="needs_review",
            )
        )
        db.commit()

        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "crawl_already_found_self"
        assert result["auto_applied"] is True
        target = db.get(Contact, target_id)
        db.refresh(target)
        assert target.email == "akowal-byrne@hillcrestacademy.example"
    finally:
        db.close()


def test_no_samples_no_candidate():
    db = SessionLocal()
    try:
        run_id, _ = _setup(db, target_org_domain="hillcrestacademy.example", samples=[])
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "no_candidate"
    finally:
        db.close()


def test_target_with_email_is_skipped(monkeypatch):
    db = SessionLocal()
    try:
        run_id, target_id = _setup(
            db, target_org_domain="hillcrestacademy.example", samples=[]
        )
        # Give the target an email after setup.
        t = db.get(Contact, target_id)
        t.email = "already@hillcrestacademy.example"
        db.commit()
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "target_already_has_email"
    finally:
        db.close()


def test_below_min_samples_no_inference(monkeypatch):
    """One sample is below the 3-sample default floor → no pattern."""
    monkeypatch.setattr(
        enrichment_followup, "validate_email", lambda e: ValidationStatus.UNKNOWN
    )
    db = SessionLocal()
    try:
        run_id, _ = _setup(
            db,
            target_org_domain="hillcrestacademy.example",
            samples=[("John Doe", "jdoe@hillcrestacademy.example")],
        )
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        # No vendor key either → no candidate.
        assert result["status"] == "no_candidate"
    finally:
        db.close()


def test_domain_inferred_from_samples_when_org_has_none(monkeypatch):
    """Org has no domain set; we derive it from the majority of sample emails."""
    monkeypatch.setattr(
        enrichment_followup, "validate_email", lambda e: ValidationStatus.UNKNOWN
    )
    db = SessionLocal()
    try:
        run_id, _ = _setup(
            db,
            target_org_domain=None,
            samples=[
                ("John Doe", "jdoe@hillcrestacademy.example"),
                ("Jane Smith", "jsmith@hillcrestacademy.example"),
                ("Bob Jones", "bjones@hillcrestacademy.example"),
            ],
        )
        result = enrichment_followup.run_enrichment_followup(db, run_id)
        assert result["status"] == "candidate_added"
        assert result["candidate"].endswith("@hillcrestacademy.example")
    finally:
        db.close()
