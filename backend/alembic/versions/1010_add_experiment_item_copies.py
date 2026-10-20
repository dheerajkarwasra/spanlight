"""Keep a copy of the dataset item each experiment item was scored against.

Revision ID: 1010
Revises: 1009
Create Date: 2026-10-20

`experiment_items` gains `input`, `expected_output` and `item_metadata`: the dataset item as it
was when the run scored it, so editing or deleting the item later never changes what a past run
means. A row written before this revision has all three NULL and is shown as a legacy row.
`experiment_items_copy_check` makes the copy all or nothing: either `input` and `item_metadata`
are set (`expected_output` may be NULL, as on the item), or all three are NULL.

Choices worth knowing about:

* Every step runs outside the migration's transaction (`autocommit_block()`), as 0700 does. The
  nullable column adds with no default and the `NOT VALID` CHECK change only the catalog, under
  an ACCESS EXCLUSIVE lock held for milliseconds. `VALIDATE CONSTRAINT` then scans the table in
  its own statement under SHARE UPDATE EXCLUSIVE, with the worker bypass set for that statement's
  transaction only, so every project's rows are checked. Each step waits at most 5 seconds for its
  lock (`lock_timeout`). Every existing row is all-NULL, so validation passes.
* Every step is safe to repeat (`IF NOT EXISTS`, drop-then-add of the constraint).

Downgrade drops the constraint and the three columns. Every stored copy is lost: runs made after
this revision become legacy rows again. That loss is the accepted cost of rolling back.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1010"
down_revision: str | None = "1009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
COPY_CHECK = "experiment_items_copy_check"
COPY_RULE = (
    "(input IS NOT NULL AND item_metadata IS NOT NULL) "
    "OR (input IS NULL AND expected_output IS NULL AND item_metadata IS NULL)"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE experiment_items ADD COLUMN IF NOT EXISTS input jsonb, "
            "ADD COLUMN IF NOT EXISTS expected_output jsonb, "
            "ADD COLUMN IF NOT EXISTS item_metadata jsonb, "
            f"DROP CONSTRAINT IF EXISTS {COPY_CHECK}, "
            f"ADD CONSTRAINT {COPY_CHECK} CHECK ({COPY_RULE}) NOT VALID"
        )
        _with_bypass(f"ALTER TABLE experiment_items VALIDATE CONSTRAINT {COPY_CHECK};")
        op.execute("RESET lock_timeout")


# Copied on purpose from 0700: migrations do not import app code or each other, so a later change
# to a shared helper cannot rewrite what a shipped one did.
def _with_bypass(statement: str) -> None:
    """Run `statement` in one `DO` block with the worker bypass set for its transaction only."""
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            {statement}
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            f"ALTER TABLE experiment_items DROP CONSTRAINT IF EXISTS {COPY_CHECK}, "
            "DROP COLUMN IF EXISTS item_metadata, "
            "DROP COLUMN IF EXISTS expected_output, "
            "DROP COLUMN IF EXISTS input"
        )
        op.execute("RESET lock_timeout")
