"""review_actions.contact_id → contacts.id : ON DELETE CASCADE

Revision ID: 0007_review_actions_cascade
Revises: 0006_source_follow_external
Create Date: 2026-05-14

The upstream "delete run" endpoint (45cc68d) was written assuming review
history followed the parent contact when a run is deleted. The FK was
authored without ON DELETE CASCADE, so deleting any run whose contacts
had been reviewed (approved/rejected/edited) crashed with a foreign-key
violation. Bulk delete on /runs surfaced the gap: 8 of 50 runs failed.

Drop and re-add the constraint with CASCADE. No data loss — the
constraint definition changes, the existing rows are untouched.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_review_actions_cascade"
down_revision: Union[str, None] = "0006_source_follow_external"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "review_actions_contact_id_fkey",
        "review_actions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "review_actions_contact_id_fkey",
        "review_actions",
        "contacts",
        ["contact_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "review_actions_contact_id_fkey",
        "review_actions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "review_actions_contact_id_fkey",
        "review_actions",
        "contacts",
        ["contact_id"],
        ["id"],
    )
