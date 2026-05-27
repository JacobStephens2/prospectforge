"""Contact enrichment: contacts.parent_contact_id + prospect_runs.enrichment_for_contact_id

Revision ID: 0008_contact_enrichment_links
Revises: 0007_review_actions_cascade
Create Date: 2026-05-27

Adds the FK chain that powers the "find contact info for this contact"
feature:

- prospect_runs.enrichment_for_contact_id: when set, the run was started
  as an enrichment for that specific contact (vs a generic source crawl).
  ON DELETE SET NULL so deleting the parent contact doesn't cascade-blow
  the enrichment run's history.

- contacts.parent_contact_id: when set, this contact row was produced by
  an enrichment run and is offered as a suggestion against the parent.
  ON DELETE SET NULL so the child rows persist even if the parent is
  deleted (they're still valid contacts on their own).

Both are nullable + indexed for the lookup patterns the new API uses.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_contact_enrichment_links"
down_revision: Union[str, None] = "0007_review_actions_cascade"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("parent_contact_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "contacts_parent_contact_id_fkey",
        "contacts",
        "contacts",
        ["parent_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_contacts_parent_contact_id",
        "contacts",
        ["parent_contact_id"],
        postgresql_where=sa.text("parent_contact_id IS NOT NULL"),
    )

    op.add_column(
        "prospect_runs",
        sa.Column(
            "enrichment_for_contact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "prospect_runs_enrichment_for_contact_id_fkey",
        "prospect_runs",
        "contacts",
        ["enrichment_for_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_prospect_runs_enrichment_for_contact_id",
        "prospect_runs",
        ["enrichment_for_contact_id"],
        postgresql_where=sa.text("enrichment_for_contact_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prospect_runs_enrichment_for_contact_id",
        table_name="prospect_runs",
    )
    op.drop_constraint(
        "prospect_runs_enrichment_for_contact_id_fkey",
        "prospect_runs",
        type_="foreignkey",
    )
    op.drop_column("prospect_runs", "enrichment_for_contact_id")
    op.drop_index("ix_contacts_parent_contact_id", table_name="contacts")
    op.drop_constraint(
        "contacts_parent_contact_id_fkey", "contacts", type_="foreignkey"
    )
    op.drop_column("contacts", "parent_contact_id")
