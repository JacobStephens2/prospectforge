"""crm cache + linking columns use text, not int — matches AgCode varchar(10)

Revision ID: 0002_crm_id_text
Revises: 0001_initial
Create Date: 2026-05-12

tblAgency.AgCode and tblAgentUsers.agcode are varchar(10) in CRM's
schema (verified against prod via the prospectforge_readonly user).
Migration 0001 modeled crm_id as Integer because the spec described
agencies as having numeric IDs — corrected here before any data is loaded.

tblAgentUsers.id is still int auto_increment, so crm_agent_users_cache.
crm_id stays Integer.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_crm_id_text"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # crm_agencies_cache.crm_id: int → text (PK)
    op.execute(
        "ALTER TABLE crm_agencies_cache "
        "ALTER COLUMN crm_id TYPE text USING crm_id::text"
    )

    # crm_agent_users_cache.agency_id: int → text (matches tblAgency.AgCode)
    op.execute(
        "ALTER TABLE crm_agent_users_cache "
        "ALTER COLUMN agency_id TYPE text USING agency_id::text"
    )

    # organizations.crm_agency_id: int → text
    op.execute(
        "ALTER TABLE organizations "
        "ALTER COLUMN crm_agency_id TYPE text USING crm_agency_id::text"
    )

    # contacts.crm_agency_id (new column) — set when an email's domain
    # matches a CRM agency's URL domain, even without an organization
    # link. Useful for the review queue's "previously a customer" badge.
    op.add_column("contacts", sa.Column("crm_agency_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "crm_agency_id")
    # Downgrade only works if all values are numeric strings; otherwise the
    # cast will fail loudly — which is what we want.
    op.execute(
        "ALTER TABLE organizations "
        "ALTER COLUMN crm_agency_id TYPE integer USING crm_agency_id::integer"
    )
    op.execute(
        "ALTER TABLE crm_agent_users_cache "
        "ALTER COLUMN agency_id TYPE integer USING agency_id::integer"
    )
    op.execute(
        "ALTER TABLE crm_agencies_cache "
        "ALTER COLUMN crm_id TYPE integer USING crm_id::integer"
    )
