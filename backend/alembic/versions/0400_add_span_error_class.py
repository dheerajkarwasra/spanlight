"""Add the error class of a failed span.

Revision ID: 0400
Revises: 0307
Create Date: 2026-10-10

`spans.error_class` says what kind of failure a span is: `auth`, `rate_limit`, `timeout`,
`context_length`, `content_filter`, `provider_5xx`, `network`, `client` or `unknown`. Ingestion
computes it (`app.ingest.error_class.classify_error`) from the span's status, its status message
and the status attributes; it is NULL exactly when the span did not fail. The trace explorer
filters on it and the insight detectors count failures by it.

Choices worth knowing about:

* The column is nullable with no default, so adding it rewrites nothing. Spans ingested before
  this migration keep NULL: their class is unknown, not `unknown`, and nothing backfills it.
* The CHECK lists the values `ErrorClass` defines; a new class needs a migration that widens it.
  It also refuses a class on a span that did not fail. The converse (a failed span always has a
  class) cannot be checked, since spans written before this migration have none.
* Every step runs outside the migration's transaction (`autocommit_block()`), each statement
  committing on its own, as 0202 does for `spans`. Adding the column and the `NOT VALID`
  constraint changes only the catalog, under an ACCESS EXCLUSIVE lock held for milliseconds; the
  migration waits at most 5 seconds for it (`lock_timeout`) rather than stall every ingest queued
  behind the wait. `VALIDATE CONSTRAINT` then scans the table in its own statement under SHARE
  UPDATE EXCLUSIVE, which lets ingestion and reads continue.
* `spans_project_error_class_started_idx (project_id, error_class, started_at DESC)` is partial,
  `WHERE error_class IS NOT NULL`: only failed spans enter it, which keeps it small. It is built
  `CONCURRENTLY`, as 0302 builds its index, so ingestion keeps writing during the build. An
  interrupted build leaves an INVALID index behind; the leftover is dropped first. Every other
  step is safe to repeat as well, so running the migration again after a failure is safe.

Downgrade drops the index (concurrently), then the column with its CHECK, under the same 5 second
lock wait cap.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0400"
down_revision: str | None = "0307"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "spans_project_error_class_started_idx"
LOCK_TIMEOUT = "5s"
CHECK = "spans_error_class_check"
ERROR_CLASSES = (
    "auth",
    "rate_limit",
    "timeout",
    "context_length",
    "content_filter",
    "provider_5xx",
    "network",
    "client",
    "unknown",
)


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
    values = ", ".join(f"'{value}'" for value in ERROR_CLASSES)
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE spans ADD COLUMN IF NOT EXISTS error_class text")
        op.execute(f"ALTER TABLE spans DROP CONSTRAINT IF EXISTS {CHECK}")
        op.execute(
            f"ALTER TABLE spans ADD CONSTRAINT {CHECK} "
            f"CHECK (error_class IS NULL OR (status = 'error' AND error_class IN ({values}))) "
            "NOT VALID"
        )
        op.execute("RESET lock_timeout")
        _validate("spans", CHECK)
        _build_index(
            INDEX,
            "ON spans (project_id, error_class, started_at DESC) WHERE error_class IS NOT NULL",
        )


def _build_index(name: str, definition: str) -> None:
    """Create `name` concurrently unless a valid one exists; drop an INVALID leftover first."""
    state = _index_is_valid(name)
    if state is True:
        return
    if state is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY {name} {definition}")


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
        op.execute(
            f"ALTER TABLE spans DROP CONSTRAINT IF EXISTS {CHECK}, "
            "DROP COLUMN IF EXISTS error_class"
        )
        op.execute("RESET lock_timeout")
