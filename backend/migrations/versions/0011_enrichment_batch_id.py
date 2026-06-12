"""prospect_runs.enrichment_batch_id — group bulk-enrich runs on /runs

Revision ID: 0011_enrichment_batch_id
Revises: 0010_run_failure_reason
Create Date: 2026-06-12

A single "Enrich N visible" click on /review starts one enrichment run per
contact. On the /runs overview that produced N separate rows, drowning the
real source runs. This column tags every run from one bulk request with a
shared UUID so the list view can collapse them into a single expandable
batch row.

NULL for one-off (single-contact) enrichments and for normal source runs —
those still render as individual rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_enrichment_batch_id"
down_revision: Union[str, None] = "0010_run_failure_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prospect_runs",
        sa.Column("enrichment_batch_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_prospect_runs_enrichment_batch_id",
        "prospect_runs",
        ["enrichment_batch_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_prospect_runs_enrichment_batch_id", table_name="prospect_runs")
    op.drop_column("prospect_runs", "enrichment_batch_id")
