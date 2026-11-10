"""Index the traces that have errors, newest first.

Revision ID: 1047
Revises: 1046
Create Date: 2026-11-10

`traces_project_errored_started_idx` serves the trace list and its histogram when they are
filtered to traces with errors. Without it they read every trace of the window to find the few
that failed, so the healthier the project, the slower the page: with 300 errored traces among
3 000 000 over 30 days the list took about 80 ms and the histogram about 85 ms; with the index
both take about 1 ms, and so does a value sort of the errored traces.

Choices worth knowing about:

* It is partial (`error_count > 0`), so it holds only the errored traces: 864 kB for the case
  above. Its key is the list's order, `started_at DESC, trace_id DESC`, so a page is read in
  order and needs no sort.
* `error_count` is rewritten by every ingest batch's totals recompute, and an index on a
  rewritten column ends in-page (HOT) updates of the row. Postgres compares the values, though:
  an update that leaves `error_count` as it was stays in the page, so only a batch that changes
  a trace's error count pays (its first error, or one more). Measured through the ingest
  pipeline: every update of an error-free trace stayed in its page, as before.
* The index is built `CONCURRENTLY`, so ingestion continues during the migration; that cannot run
  inside a transaction, hence `autocommit_block()`, as in 1020. No `lock_timeout`: a concurrent
  build never blocks writes.
* An interrupted concurrent build leaves an INVALID index behind, which `IF NOT EXISTS` would
  keep. The upgrade drops such a leftover first, so running it again is safe.

Downgrade drops the index concurrently.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1047"
down_revision: str | None = "1046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "traces_project_errored_started_idx"


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
        if _index_is_valid(INDEX) is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
            "ON traces (project_id, started_at DESC, trace_id DESC) WHERE error_count > 0"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
