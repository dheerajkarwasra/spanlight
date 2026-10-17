"""Add the `error_calls` alert metric.

Revision ID: 0705
Revises: 0704
Create Date: 2026-10-16

An alert rule can now count failed llm calls (`error_calls`), optionally of one error class or
rate-limit kind. `alert_rules.metric` is the `alert_metric` enum of 0303, so the value is added
to it. The new filters live in the rule's `filters` JSON and need no change to the schema.

Choices worth knowing about:

* `ALTER TYPE ... ADD VALUE` runs outside the migration's transaction (`autocommit_block()`):
  a value added inside a transaction cannot be used until it commits, and outside one the
  statement is safe to repeat (`IF NOT EXISTS`). It changes only the catalog; no row is read or
  rewritten.

Downgrade is lossy for one metric: Postgres cannot remove an enum value, so the type is built
again without it. The rules that count failed calls are deleted first, with the worker bypass so
every project's rows are reached, together with their states and events (`ON DELETE CASCADE`).
The 0303 CHECK that ties `spend` to budget rules compares the column with the old type, so it is
dropped and added back around the column's type change, in the same transaction. The lock wait
on `alert_rules` is capped at 5 seconds; if it times out, run the downgrade again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0705"
down_revision: str | None = "0704"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
SPEND_CHECK = "alert_rules_spend_for_kind"
METRICS_0303 = ("error_rate", "p95_ms", "ttft_p95_ms", "cost_usd", "llm_calls", "tokens", "spend")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE alert_metric ADD VALUE IF NOT EXISTS 'error_calls'")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _with_bypass("DELETE FROM alert_rules WHERE metric = 'error_calls';")
    metrics = ", ".join(f"'{metric}'" for metric in METRICS_0303)
    op.execute(f"ALTER TABLE alert_rules DROP CONSTRAINT {SPEND_CHECK}")
    op.execute("ALTER TYPE alert_metric RENAME TO alert_metric_0705")
    op.execute(f"CREATE TYPE alert_metric AS ENUM ({metrics})")
    op.execute(
        "ALTER TABLE alert_rules "
        "ALTER COLUMN metric TYPE alert_metric USING metric::text::alert_metric, "
        f"ADD CONSTRAINT {SPEND_CHECK} CHECK ((metric = 'spend') = (kind = 'budget'))"
    )
    op.execute("DROP TYPE alert_metric_0705")


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
