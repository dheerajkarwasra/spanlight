"""Add `reason` and `reason_detail` to annotation queue items.

Revision ID: 1000
Revises: 0900
Create Date: 2026-10-17

A review policy fills a queue every night with the traces worth reading: open Doctor findings,
errors, cost outliers and a random share. Each item records why it was queued, so a reviewer
knows what to look for and the queue page can show progress per reason.

`reason` is one of `manual`, `doctor_finding`, `error`, `cost_outlier`, `random`; every item
queued by hand before this revision reads `manual`. `reason_detail` is a JSON object of numbers,
ids and the trace name (a finding id, an error count, the cost and the p99 it exceeded), never
payload text; a manual add stores no detail.

Choices worth knowing about:

* `reason text NOT NULL DEFAULT 'manual'` is a constant default, so adding the column rewrites
  nothing on Postgres 11+. The two CHECKs are added `NOT VALID` and validated in their own
  statements under SHARE UPDATE EXCLUSIVE, so queue work continues during the scan. The table
  has FORCE ROW LEVEL SECURITY, so validation runs through the bypass wrapper 0505 and 0508 use.
* Every statement runs outside the migration's transaction (`autocommit_block()`), each
  committing on its own, as 0501 and 0508 do: the column add takes an ACCESS EXCLUSIVE lock for
  milliseconds, and the migration waits at most 5 seconds for it (`lock_timeout`) rather than
  stall every claim queued behind the wait.
* `annotation_queue_items_queue_reason_idx (queue_id, reason, status)` serves the per-reason
  progress on the queue page. It is built `CONCURRENTLY`, which cannot run inside a transaction;
  an interrupted build leaves an INVALID index behind, so the leftover is dropped first and
  running the migration again is safe. Every other step is safe to repeat too (`IF NOT EXISTS`,
  drop-then-add of the constraints).

Downgrade drops the index (concurrently), the two CHECKs and both columns. The reasons already
stored are lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1000"
down_revision: str | None = "0900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "annotation_queue_items"
INDEX = "annotation_queue_items_queue_reason_idx"
LOCK_TIMEOUT = "5s"
CHECKS = {
    "annotation_queue_items_reason_check": (
        "reason IN ('manual', 'doctor_finding', 'error', 'cost_outlier', 'random')"
    ),
    "annotation_queue_items_reason_detail_check": "jsonb_typeof(reason_detail) = 'object'",
}


def _index_is_valid(name: str) -> bool | None:
    """True if the index exists and is valid, False if it exists but is INVALID, None if absent.

    Offline (`--sql`) there is no database to ask, so the answer is None and the statements below
    are the unconditional ones.
    """
    context = op.get_context()
    if context.as_sql:
        return None
    row = context.bind.execute(  # type: ignore[union-attr]  # bind is set when not as_sql
        sa.text(
            "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND c.relnamespace = current_schema()::regnamespace"
        ),
        {"name": name},
    ).first()
    return None if row is None else bool(row[0])


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            f"ALTER TABLE {TABLE} "
            "ADD COLUMN IF NOT EXISTS reason text NOT NULL DEFAULT 'manual', "
            "ADD COLUMN IF NOT EXISTS reason_detail jsonb"
        )
        drops = ", ".join(f"DROP CONSTRAINT IF EXISTS {name}" for name in CHECKS)
        op.execute(f"ALTER TABLE {TABLE} {drops}")
        adds = ", ".join(
            f"ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID"
            for name, expression in CHECKS.items()
        )
        op.execute(f"ALTER TABLE {TABLE} {adds}")
        op.execute("RESET lock_timeout")
        for name in CHECKS:
            _validate(TABLE, name)
        state = _index_is_valid(INDEX)
        if state is True:
            return
        if state is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY {INDEX} ON {TABLE} (queue_id, reason, status)")


# Copied on purpose into each migration that validates: migrations do not import app code or
# each other, so a later change to a shared helper cannot rewrite what a shipped one did.
def _validate(table: str, constraint: str) -> None:
    """Validate `constraint` against every row of `table`, whatever row-level security hides.

    Migrations run as the table owner, and FORCE ROW LEVEL SECURITY applies the policies to it:
    Postgres validates a foreign key with a query that sees only the rows the policies let
    through. (A CHECK is validated by scanning the table, which the policies do not filter; it
    goes through here too, so every validation follows one rule.) The worker bypass is set for
    the statement's own transaction and the caller's value restored afterwards; on an error the
    transaction aborts and discards the setting by itself. One `DO` block, so the setting and the
    validation share a transaction even inside `autocommit_block()`.
    """
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            ALTER TABLE {table} VALIDATE CONSTRAINT {constraint};
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        drops = ", ".join(f"DROP CONSTRAINT IF EXISTS {name}" for name in CHECKS)
        op.execute(
            f"ALTER TABLE {TABLE} {drops}, DROP COLUMN IF EXISTS reason_detail, "
            "DROP COLUMN IF EXISTS reason"
        )
        op.execute("RESET lock_timeout")
