"""agency status now uses booking recency, not tblAgency.SalesStatus

Revision ID: 0003_agency_status_recency
Revises: 0002_crm_id_text
Create Date: 2026-05-12

Per Jacob (2026-05-12), CRM's notion of customer/prospect/inactive isn't a
flag on tblAgency — it's derived from tblBooking history:

  - customer: ≥1 non-cancelled booking with GroupID within last 24 months
  - inactive: had bookings but nothing within last 24 months
  - prospect: in tblAgency/tblAgentUsers but never had a booking

This migration adds last_booking_at so the cache carries the raw timestamp.
The `status` column is repurposed to hold one of {customer, inactive, prospect}
computed by the sync. Old SalesStatus int values get wiped (the cache only
has 6,645 rows synced today; full re-sync brings them back with the new
classification).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agency_status_recency"
down_revision: Union[str, None] = "0002_crm_id_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "crm_agencies_cache",
        sa.Column("last_booking_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing rows hold raw SalesStatus integers that no longer mean what
    # the code reads. Blank the column; the next sync writes the new values.
    op.execute("UPDATE crm_agencies_cache SET status = NULL")
    op.create_index(
        "ix_crm_agencies_status",
        "crm_agencies_cache",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_agencies_status", table_name="crm_agencies_cache")
    op.drop_column("crm_agencies_cache", "last_booking_at")
