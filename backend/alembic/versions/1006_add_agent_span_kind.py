"""Add `agent` to the `span_kind` enum.

Revision ID: 1006
Revises: 1005
Create Date: 2026-10-19

Agent spans (GenAI `invoke_agent` / `create_agent`, OpenInference `AGENT`, Traceloop `agent`)
were stored as `chain`; from this release they are `agent`. Spans stored earlier keep `chain`
(no backfill).

`ALTER TYPE ... ADD VALUE` runs outside the migration's transaction (`autocommit_block()`), as in
0803: a value added inside a transaction cannot be used until it commits, and outside one the
statement is safe to repeat (`IF NOT EXISTS`). It changes only the catalog; no row is read or
rewritten.

Postgres cannot drop an enum value, so the downgrade moves the rows back instead, which the
previous release can read: `agent` spans become `chain`, and the `agent` rollup rows are deleted
(derived data; the rollup job rewrites the range). Both tables force row-level security, so the
statements run with the worker bypass set. The value itself stays in the type, unused; upgrading
again is a no-op. The `UPDATE` scans `spans`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1006"
down_revision: str | None = "1005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Copied on purpose into each migration that needs it: migrations do not import app code or each
# other, so a later change to a shared helper cannot rewrite what a shipped one did.
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


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE span_kind ADD VALUE IF NOT EXISTS 'agent' AFTER 'chain'")


def downgrade() -> None:
    _with_bypass(
        "UPDATE spans SET kind = 'chain' WHERE kind = 'agent'; "
        "DELETE FROM span_rollups_hourly WHERE kind = 'agent';"
    )
