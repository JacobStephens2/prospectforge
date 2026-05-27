"""ProspectForge ORM models. SQLAlchemy 2.0 / typed Mapped style.

Schema follows spec §5 (with v2 deltas merged into the v1 implicits). Keep the
narrative grouping below; reorganize into separate modules only if a single
table grows non-trivial behavior. Until then, one file is reviewer-friendly.

The migration in `migrations/versions/0001_initial.py` is the source of truth
for the physical schema (extensions, partial indexes, `UNIQUE NULLS NOT
DISTINCT` constraints). These models are the application-layer view.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# ---------- identity & sources ----------


class User(Base):
    """Real user accounts so the audit trail can be trusted (spec §5 v2)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # admin | reviewer | sales | bdr
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Source(Base):
    """A curated source URL Morgan registers (a Northwind page, a TTA directory, etc.).

    `crawl_tier` is per-source per §6.1/§6.2: 1 = httpx default, 2 = Firecrawl
    (feature-flagged on per-source).
    """

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    segment_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    follow_external_links: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_external_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------- run lifecycle ----------


class PromptVersion(Base):
    """Locked prompt text stamped onto every LLM-produced row. Never delete."""

    __tablename__ = "prompt_versions"

    name: Mapped[str] = mapped_column(Text, primary_key=True)  # extract | classify
    version: Mapped[str] = mapped_column(Text, primary_key=True)  # semver or hash
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False, default="claude-sonnet-4-5")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProspectRun(Base):
    """One crawl/extract/dedup/score run against one Source.

    Spec §5 v2: adds the budget triplet (max_cost_cents, estimated, approved_by/at)
    and switches created_by to a real users FK.
    """

    __tablename__ = "prospect_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # pending | running | over_budget | failed | completed
    prompt_version_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    max_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    budget_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    budget_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    pages_crawled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # When set, this run was started as an enrichment for a specific contact
    # (per-contact "find contact info" feature). Contacts produced by the run
    # are linked back via contacts.parent_contact_id.
    enrichment_for_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunJob(Base):
    """Idempotency + retry tracking for each worker step (spec §5 v2)."""

    __tablename__ = "run_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_runs.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(Text, nullable=False)  # crawl | extract | dedup | score | export
    rq_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# ---------- crawl + extract ----------


class SourcePage(Base):
    """A page fetched during a run. (run_id, url) is unique per §5.2."""

    __tablename__ = "source_pages"
    __table_args__ = (UniqueConstraint("run_id", "url", name="uq_source_pages_run_url"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_runs.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # fetched | blocked_ssrf | blocked_robots | blocked_budget | error
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Organization(Base):
    """An extracted organization (school, district, agency)."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_pages.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    address1: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    zip: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True, default="USA")
    school_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    school_grade: Mapped[str | None] = mapped_column(Text, nullable=True)
    school_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    # new | possible_match | weak_match | existing_customer | suppressed | exact
    dedupe_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    crm_agency_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Contact(Base):
    """An extracted contact. (run_id, lower(email)) is unique NULLS NOT DISTINCT
    where email is not null — see migration for the partial unique index.
    """

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_runs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_pages.id"), nullable=True
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    alt_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # verified | pattern_guess | null
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)

    evidence_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_verified: Mapped[str] = mapped_column(Text, nullable=False, default="unverified")
    # verified | fuzzy | unverified | failed
    evidence_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    segment_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # pending | approved | rejected | edited | needs_review
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    dedupe_status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    dedupe_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    crm_agent_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crm_agency_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    score_role_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score_state_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score_has_direct_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score_has_phone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score_has_travel_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set when this row was produced by an enrichment run targeting the
    # parent. Lets the UI display child rows as suggestions under the parent
    # instead of as standalone results.
    parent_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------- suppressions + exports ----------


class Suppression(Base):
    """Manual block-list (spec §5.3, §12.1)."""

    __tablename__ = "suppressions"
    __table_args__ = (UniqueConstraint("type", "value_lower", name="uq_suppressions_type_value"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(Text, nullable=False)  # email | domain
    value: Mapped[str] = mapped_column(Text, nullable=False)
    # Stored lowercase for the unique constraint; populated by app/trigger.
    value_lower: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Export(Base):
    """A CSV that was generated for a run."""

    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_runs.id"), nullable=False
    )
    csv_format: Mapped[str] = mapped_column(Text, nullable=False, default="main")
    # main | bdr_verify
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExportRow(Base):
    """One CSV row emitted, retained indefinitely to block re-export (spec §5 v2, §11.2)."""

    __tablename__ = "export_rows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    export_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exports.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id"), nullable=False
    )
    row_hash: Mapped[str] = mapped_column(Text, nullable=False)
    exported_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReviewAction(Base):
    """Audit log of every reviewer decision."""

    __tablename__ = "review_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    # approve | reject | edit | override_dedupe | override_export_dup
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------- CRM Bridge caches ----------


class MagicLinkToken(Base):
    """One row per magic-link request. Single-use, 15-minute window."""

    __tablename__ = "magic_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuthSession(Base):
    """Server-side session record. The cookie carries the `token`; this row
    is what makes it authoritative — revoked_at supports logout-from-here
    and logout-everywhere without trusting the client."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class CRMAgenciesCache(Base):
    """Mirror of crm_db.tblAgency for dedupe (spec §10).

    `crm_id` is tblAgency.AgCode (varchar(10) in CRM).
    `status` is derived at sync time from booking recency, not from any
    CRM column directly — one of {customer, inactive, prospect}:
        customer: ≥1 non-cancelled booking with GroupID in last 24 months
        inactive: had bookings but nothing in last 24 months
        prospect: in tblAgency but never had a booking
    `last_booking_at` is the raw MAX(tblBooking.DateBooked) for audit.
    """

    __tablename__ = "crm_agencies_cache"

    crm_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_booking_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CRMAgentUsersCache(Base):
    """Mirror of crm_db.tblAgentUsers for email-level dedupe.

    CRM's own Lead::isDuplicate() checks tblAgentUsers (not tblGroupInfo),
    so this is the canonical email-collision target — a correction we caught
    while drafting the CSV proposal that spec §10 implies but doesn't spell out.

    `crm_id` is tblAgentUsers.id (int auto_increment in CRM).
    `agency_id` is tblAgentUsers.agcode (varchar(10), links to tblAgency.AgCode).
    """

    __tablename__ = "crm_agent_users_cache"

    crm_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    alt_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
