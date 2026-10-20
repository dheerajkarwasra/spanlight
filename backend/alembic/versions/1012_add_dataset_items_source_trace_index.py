"""Index dataset items by the trace they were copied from.

Revision ID: 1012
Revises: 1011
Create Date: 2026-10-20

`dataset_items_source_trace_idx` serves "which datasets already hold this trace" lookups
(`source_trace_id = ANY (...)`). It is partial: items typed in by hand have no source trace and
are left out. `texteq` is leakproof, so under the table's FORCE row-level security the app role
can still use the index.

Choices worth knowing about:

* The index is built `CONCURRENTLY`, so writes to `dataset_items` continue during the migration;
  that cannot run inside a transaction, hence `autocommit_block()`, as in 1009. No `lock_timeout`:
  a concurrent build never blocks writes, so a timeout would only fail the deploy behind a long
  transaction.
* An interrupted concurrent build leaves an INVALID index behind, which `IF NOT EXISTS` would
  keep. The upgrade drops such a leftover first, so running it again is safe.

Downgrade drops the index concurrently.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1012"
down_revision: str | None = "1011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "dataset_items_source_trace_idx"


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
            "ON dataset_items (source_trace_id) WHERE source_trace_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
