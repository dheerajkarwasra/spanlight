"""Add `spans.usage_rollup`, the flag of a span whose usage repeats a descendant's.

Revision ID: 0900
Revises: 0804
Create Date: 2026-10-17

An exporter can report one call's usage on two spans: an agent framework's outer span over the
provider call it wraps (Vercel AI SDK, the GenAI `invoke_agent` root), or an SDK span a caller
joined to the gateway's span by hand. Counting both doubles the trace's tokens and cost.

`usage_rollup` is derived from the stored rows alone, by the recompute every ingest batch runs
on its traces (`app.ingest.usage_rollup`): true when the span carries usage (either token count
set) and so does any of its descendants in the same trace. The span keeps every column it was
ingested with; every sum of usage or cost skips it, every count of spans keeps it.

Choices worth knowing about:

* `boolean NOT NULL DEFAULT false`: a constant default, so adding the column rewrites nothing
  on Postgres 11+, and every existing span reads as billable. There is no backfill here: rows
  stored before the upgrade are re-evaluated when a batch next touches their trace, or by
  `spanlight recompute-usage-rollups` for an operator who wants history corrected.
* No index. The flag is read beside `cost_usd` in queries already bounded by
  `(project_id, started_at)` or `(project_id, trace_id)`.
* The column add runs outside the migration's transaction (`autocommit_block()`), as 0508 does
  for `spans`: the ACCESS EXCLUSIVE lock is held for milliseconds, and the migration waits at
  most 5 seconds for it (`lock_timeout`) rather than stall every ingest queued behind the wait.
  `IF NOT EXISTS` makes a rerun after an interrupted run safe.

Downgrade drops the column; the flags are derived, so nothing is lost that an upgrade does not
recompute.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0900"
down_revision: str | None = "0804"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE spans ADD COLUMN IF NOT EXISTS usage_rollup boolean NOT NULL DEFAULT false"
        )
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE spans DROP COLUMN IF EXISTS usage_rollup")
        op.execute("RESET lock_timeout")
