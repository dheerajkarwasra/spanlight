"""Add the request hash and the finish reason of a span.

Revision ID: 0401
Revises: 0400
Create Date: 2026-10-10

`spans.request_hash` is 32 lowercase hex characters identifying what the model was asked
(`app.ingest.request_hash`): the SDK and the gateway compute it client side, ingestion computes
it otherwise, in both cases before the project's payload capture setting drops the input. Spans
with the same hash are the same request, which is how retry storms and cache opportunities are
found. `spans.finish_reason` is why the model stopped, in one vocabulary for every provider:
`stop`, `length`, `tool_calls`, `content_filter` or `other` (`app.ingest.finish_reason`); the
provider's raw value stays in `attributes`.

Choices worth knowing about:

* Both columns are nullable with no default, so adding them rewrites nothing. Spans ingested
  before this migration keep NULL: nothing backfills them.
* Each column has a CHECK, the hash on its shape and the finish reason on the canonical values (a
  new value needs a migration that widens it).
* Every step runs outside the migration's transaction (`autocommit_block()`), each statement
  committing on its own, as 0202 and 0400 do for `spans`. Adding the columns and the `NOT VALID`
  constraints changes only the catalog, under an ACCESS EXCLUSIVE lock held for milliseconds; the
  migration waits at most 5 seconds for it (`lock_timeout`) rather than stall every ingest queued
  behind the wait. Each `VALIDATE CONSTRAINT` then scans the table in its own statement under
  SHARE UPDATE EXCLUSIVE, which lets ingestion and reads continue.
* `spans_project_request_hash_started_idx (project_id, request_hash, started_at)` is partial,
  `WHERE request_hash IS NOT NULL`: spans that are not model calls carry no hash and stay out of
  it. It is built `CONCURRENTLY`, as 0302 builds its index, so ingestion keeps writing during the
  build. An interrupted build leaves an INVALID index behind; the leftover is dropped first. Every
  other step is safe to repeat as well, so running the migration again after a failure is safe.

Downgrade drops the index (concurrently), then both columns with their CHECKs, under the same
5 second lock wait cap.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0401"
down_revision: str | None = "0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "spans_project_request_hash_started_idx"
LOCK_TIMEOUT = "5s"
HASH_CHECK = "spans_request_hash_check"
FINISH_CHECK = "spans_finish_reason_check"
FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "other")


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
    values = ", ".join(f"'{value}'" for value in FINISH_REASONS)
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE spans ADD COLUMN IF NOT EXISTS request_hash text, "
            "ADD COLUMN IF NOT EXISTS finish_reason text"
        )
        op.execute(
            f"ALTER TABLE spans DROP CONSTRAINT IF EXISTS {HASH_CHECK}, "
            f"DROP CONSTRAINT IF EXISTS {FINISH_CHECK}"
        )
        op.execute(
            f"ALTER TABLE spans ADD CONSTRAINT {HASH_CHECK} "
            "CHECK (request_hash ~ '^[0-9a-f]{32}$') NOT VALID, "
            f"ADD CONSTRAINT {FINISH_CHECK} CHECK (finish_reason IN ({values})) NOT VALID"
        )
        op.execute("RESET lock_timeout")
        _validate("spans", HASH_CHECK)
        _validate("spans", FINISH_CHECK)
        _build_index(
            INDEX,
            "ON spans (project_id, request_hash, started_at) WHERE request_hash IS NOT NULL",
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
            f"ALTER TABLE spans DROP CONSTRAINT IF EXISTS {HASH_CHECK}, "
            f"DROP CONSTRAINT IF EXISTS {FINISH_CHECK}, "
            "DROP COLUMN IF EXISTS finish_reason, DROP COLUMN IF EXISTS request_hash"
        )
        op.execute("RESET lock_timeout")
