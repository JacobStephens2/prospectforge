"""Per-contact enrichment runs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db import SessionLocal
from app.models import Contact, Organization, ProspectRun, Source, User
from app.services.enrichment_run import (
    DEFAULT_ENRICHMENT_BUDGET_CENTS,
    EnrichmentNotPossible,
    list_enrichment_results,
    list_enrichment_runs,
    start_enrichment_run,
)


def _make_user(db, email_suffix: str = "") -> User:
    u = User(
        email=f"enr-{uuid.uuid4()}{email_suffix}@acme-travel.example",
        full_name="Test",
        role="admin",
    )
    db.add(u)
    db.flush()
    return u


def _make_source(db, user: User) -> Source:
    s = Source(
        name="Test source",
        url="https://example.com/",
        created_by=user.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_run(db, src: Source, user: User) -> ProspectRun:
    r = ProspectRun(
        source_id=src.id,
        created_by=user.id,
        max_cost_cents=100,
        budget_approved_by=user.id,
        budget_approved_at=datetime.now(timezone.utc),
    )
    db.add(r)
    db.flush()
    return r


def _make_contact(
    db, run: ProspectRun, *, org: Organization | None = None, **kw
) -> Contact:
    c = Contact(
        run_id=run.id,
        organization_id=org.id if org else None,
        name=kw.pop("name", "Jane Smith"),
        evidence_excerpt="x" * 30,
        **kw,
    )
    db.add(c)
    db.flush()
    return c


def test_start_enrichment_run_uses_org_website():
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = Organization(
            run_id=run.id, name="Lincoln High",
            website="https://example.com/staff/",
        )
        db.add(org)
        db.flush()
        contact = _make_contact(db, run, org=org)
        db.commit()

        enr = start_enrichment_run(
            db, contact_id=contact.id, requested_by_user_id=user.id
        )
        assert enr.enrichment_for_contact_id == contact.id
        assert enr.max_cost_cents == DEFAULT_ENRICHMENT_BUDGET_CENTS
        # Source created for the enrichment is inactive (doesn't pollute UI).
        src_row = db.get(Source, enr.source_id)
        assert src_row.active is False
        assert src_row.url == "https://example.com/staff/"
    finally:
        db.close()


def test_start_enrichment_run_falls_back_to_domain():
    """When org.website is empty but domain is set, build https://{domain}/."""
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = Organization(
            run_id=run.id, name="X High", domain="example.com",
        )
        db.add(org)
        db.flush()
        contact = _make_contact(db, run, org=org)
        db.commit()

        enr = start_enrichment_run(
            db, contact_id=contact.id, requested_by_user_id=user.id
        )
        src_row = db.get(Source, enr.source_id)
        assert src_row.url == "https://example.com/"
    finally:
        db.close()


def test_start_enrichment_run_raises_when_no_url_and_freesearch_empty(monkeypatch):
    """With no website AND no domain, the freesearch fallback runs. We
    mock it to return (None, empty-trace) to assert the truly-unseedable
    error path."""
    from app.services.serp_search import SerpTrace

    monkeypatch.setattr(
        "app.services.enrichment_run.find_person_via_freesearch_traced",
        lambda name, org_name: (None, SerpTrace(query="x", note="mock empty")),
    )
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = Organization(run_id=run.id, name="No URL High")
        db.add(org)
        db.flush()
        contact = _make_contact(db, run, org=org)
        db.commit()

        with pytest.raises(EnrichmentNotPossible):
            start_enrichment_run(
                db, contact_id=contact.id, requested_by_user_id=user.id
            )
    finally:
        db.close()


def test_start_enrichment_run_uses_freesearch_fallback(monkeypatch):
    """No website/domain but freesearch returns a URL → that becomes the
    run seed and the Source row is labeled as a web-search enrichment."""
    from app.services.serp_search import SerpTrace

    monkeypatch.setattr(
        "app.services.enrichment_run.find_person_via_freesearch_traced",
        lambda name, org_name: (
            "https://news.test/article-about-jane",
            SerpTrace(
                query='"Jane Smith" "No URL High"',
                provider="brave",
                urls=("https://news.test/article-about-jane",),
            ),
        ),
    )
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = Organization(run_id=run.id, name="No URL High")
        db.add(org)
        db.flush()
        contact = _make_contact(db, run, org=org)
        db.commit()

        enr = start_enrichment_run(
            db, contact_id=contact.id, requested_by_user_id=user.id
        )
        src_row = db.get(Source, enr.source_id)
        assert src_row.url == "https://news.test/article-about-jane"
        assert "web search" in src_row.name.lower()
    finally:
        db.close()


def test_start_enrichment_run_raises_on_missing_contact():
    db = SessionLocal()
    try:
        user = _make_user(db)
        with pytest.raises(EnrichmentNotPossible):
            start_enrichment_run(
                db, contact_id=uuid.uuid4(), requested_by_user_id=user.id
            )
    finally:
        db.close()


def test_list_enrichment_results_returns_child_contacts():
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = Organization(run_id=run.id, name="X High", domain="example.com")
        db.add(org)
        db.flush()
        parent = _make_contact(db, run, org=org, name="Parent Person")
        # Synthesize what an enrichment run would have written:
        child = _make_contact(
            db,
            run,
            org=org,
            name="Parent Person",
            email="parent@example.com",
            parent_contact_id=parent.id,
        )
        db.commit()

        out = list_enrichment_results(db, parent_contact_id=parent.id)
        assert [c.id for c in out] == [child.id]
    finally:
        db.close()


def test_list_enrichment_runs_returns_runs_targeting_contact():
    db = SessionLocal()
    try:
        user = _make_user(db)
        src = _make_source(db, user)
        run = _make_run(db, src, user)
        org = Organization(run_id=run.id, name="X", domain="example.com")
        db.add(org)
        db.flush()
        contact = _make_contact(db, run, org=org)
        db.commit()

        enr = start_enrichment_run(
            db, contact_id=contact.id, requested_by_user_id=user.id
        )
        out = list_enrichment_runs(db, parent_contact_id=contact.id)
        assert [r.id for r in out] == [enr.id]
    finally:
        db.close()
