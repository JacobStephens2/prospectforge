"""Extraction worker tests.

Most cases mock the Anthropic client so they're fast and don't burn budget.
One test is opt-in for hitting the live API — gated on PF_RUN_LIVE_EXTRACTION_TEST=1.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.db import SessionLocal
from app.models import Contact, Organization, ProspectRun, Source, SourcePage, User
from app.services.extraction import (
    ExtractedContact,
    ExtractedOrganization,
    ExtractionResult,
    extract_from_page,
)
from app.workers.extract import extract_page


# ---------- helpers ----------


def _fake_response(
    *,
    organizations: list[dict],
    input_tokens: int = 500,
    output_tokens: int = 200,
    cache_read: int = 0,
    cache_write: int = 0,
) -> MagicMock:
    """Build a mock anthropic.types.Message with one forced tool_use block."""
    tool_block = SimpleNamespace(
        type="tool_use",
        id="toolu_test",
        name="submit_extraction",
        input={"organizations": organizations},
    )
    return SimpleNamespace(
        content=[tool_block],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
        stop_reason="tool_use",
    )


def _fake_text_only_response(text: str = "I refuse.") -> MagicMock:
    """Model didn't call the tool — we treat that as a validation failure."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
    )


@pytest.fixture()
def fixture_run_and_page():
    """Create a user/source/run/page and yield (run_id, page_id, page_text)."""
    page_text = (
        "Phoenix Independent School District — 2024-25 Faculty Directory.\n"
        "Band Director: Jane Smith. Email: jane.smith@phoenixisd.org. "
        "Office: (602) 555-0100.\n"
        "Theater Director: Carlos Rivera, MFA. Reach him at crivera@phoenixisd.org.\n"
        "The high school marching band performs at the State Fair every fall."
    )

    db = SessionLocal()
    try:
        user = User(
            email=f"extract-{uuid.uuid4()}@acme-travel.example",
            full_name="Extract Test",
            role="admin",
        )
        db.add(user)
        db.flush()
        source = Source(name="Test", url="https://example.com/", created_by=user.id)
        db.add(source)
        db.flush()
        run = ProspectRun(
            source_id=source.id,
            created_by=user.id,
            max_cost_cents=5000,  # generous so budget isn't the bottleneck
            budget_approved_by=user.id,
            budget_approved_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        page = SourcePage(
            run_id=run.id,
            url="https://phoenixisd.example.org/faculty",
            status="fetched",
            content_markdown=page_text,
            http_status=200,
            content_hash="x" * 64,
        )
        db.add(page)
        db.commit()
        ids = (run.id, page.id, page_text)
    finally:
        db.close()
    return ids


# ---------- happy path ----------


def test_extraction_writes_org_and_contact(fixture_run_and_page) -> None:
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        organizations=[
            {
                "name": "Phoenix Independent School District",
                "domain": "phoenixisd.org",
                "state": "AZ",
                "school_type": "Public",
                "contacts": [
                    {
                        "name": "Jane Smith",
                        "title": "Band Director",
                        "email": "jane.smith@phoenixisd.org",
                        "phone": "(602) 555-0100",
                        "segment_tag": "K-12 band",
                        "evidence_excerpt": "Band Director: Jane Smith. Email: jane.smith@phoenixisd.org",
                    }
                ],
            }
        ]
    )

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
            client=client,
        )
    finally:
        db.close()

    assert len(result.organizations) == 1
    assert len(result.verified_contacts) == 1
    vc = result.verified_contacts[0]
    assert vc.keep is True
    assert vc.evidence.status == "verified"
    assert vc.contact.email == "jane.smith@phoenixisd.org"


def test_extract_page_worker_persists(fixture_run_and_page, monkeypatch) -> None:
    """extract_page() RQ entry: full path through DB writes."""
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        organizations=[
            {
                "name": "Phoenix Independent School District",
                "domain": "phoenixisd.org",
                "state": "AZ",
                "school_type": "Public",
                "contacts": [
                    {
                        "name": "Jane Smith",
                        "title": "Band Director",
                        "email": "jane.smith@phoenixisd.org",
                        "evidence_excerpt": "Band Director: Jane Smith. Email: jane.smith@phoenixisd.org",
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("app.services.extraction._get_client", lambda: client)

    result = extract_page(str(page_id))
    assert result["status"] == "ok"
    assert result["orgs_written"] == 1
    assert result["contacts_written"] == 1

    db = SessionLocal()
    try:
        orgs = db.query(Organization).filter(Organization.run_id == run_id).all()
        assert len(orgs) == 1
        assert orgs[0].name == "Phoenix Independent School District"
        contacts = db.query(Contact).filter(Contact.run_id == run_id).all()
        assert len(contacts) == 1
        assert contacts[0].evidence_verified == "verified"
        assert contacts[0].review_status == "pending"
    finally:
        db.close()


# ---------- evidence verification gates exports ----------


def test_fabricated_evidence_becomes_needs_review(fixture_run_and_page) -> None:
    """LLM invents an excerpt that doesn't appear in the page."""
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        organizations=[
            {
                "name": "Phoenix Independent School District",
                "contacts": [
                    {
                        "name": "Sarah Johnson",
                        "title": "Principal",
                        "email": "sarah.johnson@phoenixisd.org",
                        "evidence_excerpt": (
                            "Principal Sarah Johnson runs the international travel program"
                        ),
                    }
                ],
            }
        ]
    )

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
            client=client,
        )
    finally:
        db.close()

    assert len(result.verified_contacts) == 1
    vc = result.verified_contacts[0]
    assert vc.keep is False
    assert vc.evidence.status == "failed"


# ---------- deny-listed roles ----------


def test_denylisted_title_rejected_at_pydantic(fixture_run_and_page) -> None:
    """Pydantic field_validator catches it; first call's parse fails, retry runs."""
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    # First call returns a denylisted role → ValidationError.
    # Retry call returns valid data with the denylisted contact dropped.
    client.messages.create.side_effect = [
        _fake_response(
            organizations=[
                {
                    "name": "Phoenix ISD",
                    "contacts": [
                        {
                            "name": "Bob Parent",
                            "title": "PTA President",  # denylisted
                            "evidence_excerpt": "Bob Parent serves as PTA President for the district.",
                        }
                    ],
                }
            ]
        ),
        _fake_response(organizations=[{"name": "Phoenix ISD", "contacts": []}]),
    ]

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
            client=client,
        )
    finally:
        db.close()

    assert result.retried is True
    assert result.organizations[0].contacts == []


# ---------- retry on tool not called ----------


def test_retry_when_model_returns_text_instead_of_tool(fixture_run_and_page) -> None:
    """Model produced a text response instead of calling the tool."""
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    client.messages.create.side_effect = [
        _fake_text_only_response("Sorry, I can't help with that."),
        _fake_response(organizations=[{"name": "Phoenix ISD", "contacts": []}]),
    ]

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
            client=client,
        )
    finally:
        db.close()

    assert result.retried is True
    assert result.error is None  # retry succeeded
    assert client.messages.create.call_count == 2


def test_second_failure_marks_extraction_failed(fixture_run_and_page) -> None:
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    client.messages.create.side_effect = [
        _fake_text_only_response("nope"),
        _fake_text_only_response("nope again"),
    ]

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
            client=client,
        )
    finally:
        db.close()

    assert result.retried is True
    assert result.error is not None
    assert len(result.organizations) == 0


# ---------- budget enforcement ----------


def test_budget_cap_blocks_call(fixture_run_and_page) -> None:
    """Push the run to its cap; next call must raise OverBudget."""
    run_id, page_id, _ = fixture_run_and_page

    db = SessionLocal()
    try:
        run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
        run.cost_cents = run.max_cost_cents  # at the cap
        db.commit()
    finally:
        db.close()

    out = extract_page(str(page_id))
    assert out["status"] == "over_budget"


def test_extract_page_skips_unfetched_page(fixture_run_and_page) -> None:
    run_id, page_id, _ = fixture_run_and_page
    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        page.status = "blocked_ssrf"
        db.commit()
    finally:
        db.close()

    out = extract_page(str(page_id))
    assert out["status"] == "skipped"


# ---------- pricing math sanity ----------


def test_extraction_records_cost(fixture_run_and_page) -> None:
    run_id, page_id, _ = fixture_run_and_page

    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        organizations=[],
        input_tokens=10_000,
        output_tokens=2_000,
        cache_read=8_000,
    )

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
            client=client,
        )
        run = db.query(ProspectRun).filter(ProspectRun.id == run_id).first()
        # cost_cents on run reflects what we charged
        assert run.cost_cents == result.cost.total_cents
        assert run.tokens_used > 0
    finally:
        db.close()


# ---------- live API smoke test (opt-in) ----------


@pytest.mark.skipif(
    os.environ.get("PF_RUN_LIVE_EXTRACTION_TEST") != "1",
    reason="Live API test — set PF_RUN_LIVE_EXTRACTION_TEST=1 to enable.",
)
def test_live_anthropic_call(fixture_run_and_page) -> None:
    """Real round-trip against the Anthropic API. Costs real money; opt-in only."""
    run_id, page_id, page_text = fixture_run_and_page

    db = SessionLocal()
    try:
        page = db.query(SourcePage).filter(SourcePage.id == page_id).first()
        result = extract_from_page(
            db=db,
            run_id=run_id,
            source_url=page.url,
            page_text=page.content_markdown,
        )
    finally:
        db.close()

    assert result.error is None
    # The fixture page has clear band + theater contacts — extraction should
    # find at least one, and evidence verification should pass.
    assert any(vc.keep for vc in result.verified_contacts), \
        f"no verified contacts. cost={result.cost.total_cents}¢, retried={result.retried}"
