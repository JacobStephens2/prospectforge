"""seed the three permitted admin users — Jacob, Sam, Morgan

Revision ID: 0005_seed_admin_users
Revises: 0004_auth_tokens
Create Date: 2026-05-12

The whitelist for magic-link login IS this users table (rows with
active=true). Per Jacob's call, the pilot is gated to three people:

  - Jacob Stephens (IT Manager)
  - Sam Rivers  (Owner / Operator / President / GM)
  - Morgan Lee (Marketing Manager — product owner per spec §1.1)

All three are admins for the pilot; role-based gating is v1.1. Names come
from `/srv/orchestration/Acme/team.md`.

INSERT ... ON CONFLICT keeps the migration safely re-runnable; downgrade
deactivates the seeded rows rather than deleting them so any FK-linked
audit data (review_actions, etc.) survives.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005_seed_admin_users"
down_revision: Union[str, None] = "0004_auth_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SEEDS = [
    ("jstephens@acme-travel.example", "Jacob Stephens", "admin"),
    ("srivers@acme-travel.example",   "Sam Rivers", "admin"),
    ("mlee@acme-travel.example","Morgan Lee","admin"),
]


def upgrade() -> None:
    for email, name, role in _SEEDS:
        op.execute(
            f"""
            INSERT INTO users (email, full_name, role, active)
            VALUES ('{email}', '{name}', '{role}', true)
            ON CONFLICT (email) DO UPDATE
              SET full_name = EXCLUDED.full_name,
                  role      = EXCLUDED.role,
                  active    = true
            """
        )


def downgrade() -> None:
    for email, _name, _role in _SEEDS:
        op.execute(f"UPDATE users SET active = false WHERE email = '{email}'")
