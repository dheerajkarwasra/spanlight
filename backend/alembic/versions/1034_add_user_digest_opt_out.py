"""Add `users.weekly_digest_opt_out`: the user turned the weekly digest off for themselves.

Revision ID: 1034
Revises: 1033
Create Date: 2026-11-01

One flag per user, for every project they belong to; false (the digest is on) unless they turn it
off in Settings > Account or with the unsubscribe link in a digest. On Postgres 11 and later a
column with a constant default changes only the catalog, so the ALTER rewrites no rows and holds
its lock for milliseconds. The app at 1033 never reads the column and its inserts take the
default, so both versions can run during a rolling deploy. `users` is not project-scoped, so no
RLS policy changes.

Downgrade drops the column, and with it every opt-out: users who turned the digest off get it
again until they turn it off once more.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1034"
down_revision: str | None = "1033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS weekly_digest_opt_out boolean NOT NULL "
        "DEFAULT false"
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS weekly_digest_opt_out")
