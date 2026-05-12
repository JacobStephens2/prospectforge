"""initial schema — users, sources, runs, pages, orgs, contacts, exports, crm cache

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-12

Source of truth for the physical schema. SQLAlchemy models in app/models.py are
the application view; this migration is what Postgres actually executes.

Spec §5 (v2): adds users / run_jobs / export_rows, adds budget triplet to
prospect_runs, switches all created_by/reviewed_by to real users FKs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- extensions -----
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ----- users -----
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ----- sources -----
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("segment_template", sa.Text(), nullable=True),
        sa.Column("crawl_tier", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sources_active", "sources", ["active"])

    # ----- prompt_versions -----
    op.create_table(
        "prompt_versions",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False, server_default="claude-sonnet-4-5"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ----- prospect_runs -----
    op.create_table(
        "prospect_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("prompt_version_name", sa.Text(), nullable=True),
        sa.Column("prompt_version_value", sa.Text(), nullable=True),
        sa.Column("max_cost_cents", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_cents", sa.Integer(), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("budget_approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("budget_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_crawled", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_extracted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_prospect_runs_status", "prospect_runs", ["status"])
    op.create_index("ix_prospect_runs_source_id", "prospect_runs", ["source_id"])

    # ----- run_jobs -----
    op.create_table(
        "run_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prospect_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("rq_job_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_run_jobs_run_id", "run_jobs", ["run_id"])
    op.create_index("ix_run_jobs_rq_job_id", "run_jobs", ["rq_job_id"])

    # ----- source_pages -----
    op.create_table(
        "source_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prospect_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("raw_html_path", sa.Text(), nullable=True),
        sa.Column("page_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "url", name="uq_source_pages_run_url"),
    )
    op.create_index("ix_source_pages_content_hash", "source_pages", ["content_hash"])
    op.create_index("ix_source_pages_status", "source_pages", ["status"])

    # ----- organizations -----
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prospect_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_pages.id"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("address1", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("zip", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True, server_default="USA"),
        sa.Column("school_type", sa.Text(), nullable=True),
        sa.Column("school_grade", sa.Text(), nullable=True),
        sa.Column("school_size", sa.Text(), nullable=True),
        sa.Column("dedupe_status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("dedupe_reason", sa.Text(), nullable=True),
        sa.Column("crm_agency_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_organizations_run_id", "organizations", ["run_id"])
    op.create_index("ix_organizations_dedupe_status", "organizations", ["dedupe_status"])
    op.execute("CREATE INDEX ix_organizations_domain_lower ON organizations (lower(domain))")
    op.execute("CREATE INDEX ix_organizations_name_lower ON organizations (lower(name))")

    # ----- contacts -----
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prospect_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column(
            "source_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_pages.id"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("alt_email", sa.Text(), nullable=True),
        sa.Column("email_confidence", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("evidence_excerpt", sa.Text(), nullable=True),
        sa.Column("evidence_verified", sa.Text(), nullable=False, server_default="unverified"),
        sa.Column("evidence_match_score", sa.Float(), nullable=True),
        sa.Column("segment_tag", sa.Text(), nullable=True),
        sa.Column("prompt_version_name", sa.Text(), nullable=True),
        sa.Column("prompt_version_value", sa.Text(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_payload", postgresql.JSONB(), nullable=True),
        sa.Column("dedupe_status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("dedupe_reason", sa.Text(), nullable=True),
        sa.Column("crm_agent_user_id", sa.Integer(), nullable=True),
        sa.Column("score_role_match", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("score_state_match", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("score_has_direct_email", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("score_has_phone", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("score_has_travel_signal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_contacts_run_id", "contacts", ["run_id"])
    op.create_index("ix_contacts_review_status", "contacts", ["review_status"])
    op.create_index("ix_contacts_dedupe_status", "contacts", ["dedupe_status"])
    # Partial unique index per spec §5.2 — one email per (run, address), case-insensitive,
    # only enforced when email is non-null.
    op.execute(
        "CREATE UNIQUE INDEX uq_contacts_run_email "
        "ON contacts (run_id, lower(email)) WHERE email IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_contacts_email_lower ON contacts (lower(email))")

    # ----- suppressions -----
    op.create_table(
        "suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_lower", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("type", "value_lower", name="uq_suppressions_type_value"),
    )

    # ----- exports -----
    op.create_table(
        "exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prospect_runs.id"), nullable=False),
        sa.Column("csv_format", sa.Text(), nullable=False, server_default="main"),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ----- export_rows -----
    op.create_table(
        "export_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "export_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("row_hash", sa.Text(), nullable=False),
        sa.Column("exported_email", sa.Text(), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_export_rows_contact", "export_rows", ["contact_id"])
    op.create_index("idx_export_rows_email", "export_rows", ["exported_email"])

    # ----- review_actions -----
    op.create_table(
        "review_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_review_actions_contact_id", "review_actions", ["contact_id"])

    # ----- crm_agencies_cache -----
    op.create_table(
        "crm_agencies_cache",
        sa.Column("crm_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE INDEX ix_crm_agencies_name_trgm "
        "ON crm_agencies_cache USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_crm_agencies_domain_lower "
        "ON crm_agencies_cache (lower(domain))"
    )
    op.create_index("ix_crm_agencies_state", "crm_agencies_cache", ["state"])

    # ----- crm_agent_users_cache -----
    op.create_table(
        "crm_agent_users_cache",
        sa.Column("crm_id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("alt_email", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE INDEX ix_crm_agent_users_email_lower "
        "ON crm_agent_users_cache (lower(email))"
    )
    op.execute(
        "CREATE INDEX ix_crm_agent_users_alt_email_lower "
        "ON crm_agent_users_cache (lower(alt_email))"
    )

    # ----- seed prompt_versions -----
    op.execute(
        """
        INSERT INTO prompt_versions (name, version, prompt_text, model, active) VALUES
        ('classify', 'v0-stub',
         'PLACEHOLDER — replaced by Agent 4 with the real classification prompt.',
         'claude-sonnet-4-5', false),
        ('extract', 'v0-stub',
         'PLACEHOLDER — replaced by Agent 4 with the real extraction prompt.',
         'claude-sonnet-4-5', false)
        """
    )


def downgrade() -> None:
    # Drop in reverse dependency order. Indexes go with their tables.
    op.drop_table("crm_agent_users_cache")
    op.drop_table("crm_agencies_cache")
    op.drop_table("review_actions")
    op.drop_table("export_rows")
    op.drop_table("exports")
    op.drop_table("suppressions")
    op.drop_table("contacts")
    op.drop_table("organizations")
    op.drop_table("source_pages")
    op.drop_table("run_jobs")
    op.drop_table("prospect_runs")
    op.drop_table("prompt_versions")
    op.drop_table("sources")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
