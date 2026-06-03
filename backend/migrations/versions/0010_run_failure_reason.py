"""prospect_runs.failure_reason text — surface WHY a run failed in the UI

Revision ID: 0010_run_failure_reason
Revises: 0009_enrichment_context
Create Date: 2026-06-03

A failed run currently shows a dead-end UI message ("Check backend logs
for the orchestrator traceback") with no actual reason. Worse, the most
common failure isn't a crash at all — it's orphan recovery: the backend
restarted mid-run (deploy/rebuild), the orchestrator thread was killed
before it could stamp the run, and startup_recovery later swept the
zombie row to 'failed'. Those runs have no traceback to find.

This column lets the orchestrator persist its exception summary / budget
message, and lets startup_recovery record an orphan-specific explanation,
so the run detail page can tell the operator what actually happened.

NULL for clean/in-flight runs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_run_failure_reason"
down_revision: Union[str, None] = "0009_enrichment_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prospect_runs",
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prospect_runs", "failure_reason")
