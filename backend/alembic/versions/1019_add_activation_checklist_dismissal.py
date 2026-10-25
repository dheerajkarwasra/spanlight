"""Add the setup checklist dismissal: when the user hid the activation checklist.

Revision ID: 1019
Revises: 1018
Create Date: 2026-10-25

`users.activation_checklist_dismissed_at` is set by `PUT /auth/me/activation-checklist` and
cleared when the user shows the checklist again. It is per user, across every project they
belong to. `users` has no row-level security: the route writes only the caller's own row.

The column is nullable with no default, so adding it changes only the catalog and rewrites no
rows. The migration is one transaction under a 5 s lock timeout. Downgrade drops the column, which
forgets every dismissal: the checklist shows again for users whose projects are not set up.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1019"
down_revision: str | None = "1018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS activation_checklist_dismissed_at timestamptz"
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS activation_checklist_dismissed_at")
