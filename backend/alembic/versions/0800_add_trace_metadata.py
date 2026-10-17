"""Add `traces.metadata` and the `merge_trace_metadata` function.

Revision ID: 0800
Revises: 0705
Create Date: 2026-10-16

A trace carries a small map of string keys to string values (a customer, a feature, a team) that
the attribution view groups by and budgets match on. It is one `jsonb` object per trace, empty
when the client sent none; spans carry no metadata.

Choices worth knowing about:

* `traces.metadata` is `NOT NULL DEFAULT '{}'`: a constant default, so adding the column rewrites
  nothing on Postgres 11+, and every existing trace reads as `{}`. No backfill.
* `traces_metadata_object_check` keeps the value a JSON object. It is added `NOT VALID` and then
  validated in its own statement, which scans the table under SHARE UPDATE EXCLUSIVE and lets
  ingestion and reads continue.
* There is no index on `metadata`. `traces` forces row-level security, and Postgres uses a
  filter as an index condition only when its operator is leakproof (or the table has no policy);
  `@>`, `?` and `->>` on jsonb are not, so under the app role any such index could never narrow a
  read, and a plan could scan all of it across every project. Metadata filters run on the traces
  of a `started_at` window instead, through `traces_project_started_idx`, as tag filters do.
* The `traces` steps run outside the migration's transaction (`autocommit_block()`), as 0508 does
  for `spans`: each statement commits on its own, the ACCESS EXCLUSIVE lock of the column add is
  held for milliseconds, and the migration waits at most 5 seconds for it (`lock_timeout`) rather
  than stall every ingest queued behind the wait. Every step is safe to repeat (`IF NOT EXISTS`,
  drop-then-add of the CHECK), so running the migration again after an interrupted run is safe.
* `merge_trace_metadata(current, incoming)` is the cross-batch merge the trace upsert applies:
  per key the incoming value wins, a key the trace has is always updatable, and new keys are added
  in lexical order only while the trace has fewer than 32. Lexical order is Unicode code-point
  order (`COLLATE "C"`), the order Python's `sorted()` gives the ingest twin of this merge, so
  both keep the same keys whatever collation the database was created with. It is
  `LANGUAGE sql IMMUTABLE PARALLEL SAFE` and reads no table, so it stays callable by every role
  (no `REVOKE ... FROM PUBLIC`, unlike 0507): every trace upsert calls it, and a deploy that runs
  `migrate` without `ensure-app-role` must not stop ingestion. It is created after the
  autocommit block, in the transaction that stamps the revision.

Downgrade drops the function, the CHECK and the column, so every
trace's metadata is lost; the release that writes it must be rolled back first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0800"
down_revision: str | None = "0705"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
CHECK = "traces_metadata_object_check"
SIGNATURE = "merge_trace_metadata(current jsonb, incoming jsonb)"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE traces ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'"
        )
        op.execute(f"ALTER TABLE traces DROP CONSTRAINT IF EXISTS {CHECK}")
        op.execute(
            f"ALTER TABLE traces ADD CONSTRAINT {CHECK} "
            "CHECK (jsonb_typeof(metadata) = 'object') NOT VALID"
        )
        op.execute("RESET lock_timeout")
        _validate("traces", CHECK)
    _create_merge_function()


def _create_merge_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SIGNATURE} RETURNS jsonb
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
        SELECT coalesce(current, '{{}}'::jsonb) || coalesce((
            SELECT jsonb_object_agg(key, value) FROM (
                SELECT key, value FROM jsonb_each(incoming) WHERE current ? key
                UNION ALL
                SELECT key, value FROM (
                    SELECT key, value FROM jsonb_each(incoming)
                    WHERE NOT (current ? key) ORDER BY key COLLATE "C"
                    LIMIT greatest(0, 32 - (SELECT count(*) FROM jsonb_object_keys(current)))
                ) AS added
            ) AS kept
        ), '{{}}'::jsonb)
        $$
        """
    )


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
    op.execute("DROP FUNCTION IF EXISTS merge_trace_metadata(jsonb, jsonb)")
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            f"ALTER TABLE traces DROP CONSTRAINT IF EXISTS {CHECK}, DROP COLUMN IF EXISTS metadata"
        )
        op.execute("RESET lock_timeout")
