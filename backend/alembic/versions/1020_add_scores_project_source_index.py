"""Index scores that people and evaluators gave, by project and source.

Revision ID: 1020
Revises: 1019
Create Date: 2026-10-25

`scores_project_source_idx` serves the setup checklist's "evaluation" step: does this project
have a human score or an evaluator score. Without it that check read every tenant's human scores
(`scores_author_user_idx`) and every tenant's evaluator runs, which grows with the whole
installation instead of the project. It is partial: `api` scores (the app's own numbers) are
most of the table and are left out, so the index stays small.

Choices worth knowing about:

* The index is built `CONCURRENTLY`, so writes to `scores` continue during the migration; that
  cannot run inside a transaction, hence `autocommit_block()`, as in 1012. No `lock_timeout`: a
  concurrent build never blocks writes, so a timeout would only fail the deploy behind a long
  transaction.
* An interrupted concurrent build leaves an INVALID index behind, which `IF NOT EXISTS` would
  keep. The upgrade drops such a leftover first, so running it again is safe.
* `enum_eq` is not leakproof, so under the table's FORCE row-level security `source` is checked
  after the policy; the index is still chosen through its `project_id` column and its predicate.

Downgrade drops the index concurrently.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1020"
down_revision: str | None = "1019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "scores_project_source_idx"


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
            "ON scores (project_id, source) WHERE source <> 'api'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
