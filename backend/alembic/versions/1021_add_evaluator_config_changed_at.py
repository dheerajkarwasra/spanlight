"""Add when an evaluator's judge last changed, so agreement counts only that judge's verdicts.

Revision ID: 1021
Revises: 1020
Create Date: 2026-10-25

`evaluators.config_changed_at` is set when an edit changes the judge itself: its model, its prompt
version or its temperature (a credential, `max_tokens`, sampling rate, name, filter or enable change
leaves it alone). Human-judge agreement then pairs only verdicts from runs started at or after it.
NULL means no such change was recorded, so every run counts, as before; existing rows stay NULL.
`evaluators` already has row-level security, and a new column needs no policy change.

The column is nullable with no default, so adding it changes only the catalog and rewrites no
rows. The migration is one transaction under a 5 s lock timeout. Downgrade drops the column, which
forgets the change times: agreement again blends every config's verdicts.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1021"
down_revision: str | None = "1020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE evaluators ADD COLUMN IF NOT EXISTS config_changed_at timestamptz")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE evaluators DROP COLUMN IF EXISTS config_changed_at")
