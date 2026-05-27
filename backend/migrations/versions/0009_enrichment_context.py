"""prospect_runs.enrichment_context jsonb — capture SERP trace for the UI

Revision ID: 0009_enrichment_context
Revises: 0008_contact_enrichment_links
Create Date: 2026-05-27

The enrichment runs Morgan triggers from /review are a black box right
now — she clicks the button, the run completes (or doesn't), and she
sees only the page/contact counts. We need to surface:

  - what SERP query we ran (so she knows what we searched for)
  - what URLs Brave returned (so she can see whether the search hit
    the right context)
  - which one we picked as the run seed and why (org_website /
    serp_site / serp_freesearch)

All three travel together as JSON, so a single JSONB column on
prospect_runs keeps the schema simple. NULL for non-enrichment runs
(it'd be wasted noise on regular Source-triggered runs).

Brave's ToS prohibits storing raw SERP RESPONSES (the full JSON body);
we comply by persisting only (a) the query string we issued and
(b) the URL + optional title of the top N results. Same shape we'd
get from clicking a public Google search page link — not the API
payload.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_enrichment_context"
down_revision: Union[str, None] = "0008_contact_enrichment_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prospect_runs",
        sa.Column(
            "enrichment_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("prospect_runs", "enrichment_context")
