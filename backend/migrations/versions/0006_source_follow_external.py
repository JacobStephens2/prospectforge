"""sources.follow_external_links + sources.max_external_pages

Revision ID: 0006_source_follow_external
Revises: 0005_seed_admin_users
Create Date: 2026-05-13

A directory-style source like Northwind / Global Learning Collaborative lists
external member sites; the actual extractable contacts live on each
member's own domain. Default-off opt-in toggle controls whether the
orchestrator follows those external links, with a per-source cap so a
1000-school directory doesn't blow the budget on the first run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_source_follow_external"
down_revision: Union[str, None] = "0005_seed_admin_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "follow_external_links",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "max_external_pages",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("20"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "max_external_pages")
    op.drop_column("sources", "follow_external_links")
