"""Drop the two GIN indexes that row-level security keeps the app from using.

Revision ID: 1009
Revises: 1008
Create Date: 2026-10-17

`traces_tags_idx` (0001, `gin (tags)`) and `insights_evidence_trace_ids_idx` (0402,
`gin ((evidence -> 'trace_ids') jsonb_path_ops)`) serve only containment and overlap (`@>`, `&&`).
Both tables have FORCE row-level security and the app connects as a NOSUPERUSER NOBYPASSRLS role.
Postgres applies the policy first and moves a query condition below it, into an index scan, only
when the operator's function is LEAKPROOF; `arraycontains`, `arrayoverlap` and `jsonb_contains`
are not. So the app never reads either index (its plans use `traces_project_started_idx` or a
sequential scan and filter on the tags), while every insert still pays for the GIN entries. Only
a superuser, who bypasses the policies, gets an index scan from them.

Choices worth knowing about:

* Tag filters keep working, at the same speed as before: they already run on the window's traces
  found through `traces_project_started_idx`.
* The indexes are dropped `CONCURRENTLY`, so ingestion keeps writing during the migration; that
  cannot run inside a transaction, hence `autocommit_block()`. Code from before this revision
  never relied on the indexes, so the deploy order does not matter.

Downgrade rebuilds both indexes concurrently, dropping an INVALID leftover of an interrupted build
first, so running it again is safe.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1009"
down_revision: str | None = "1008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "traces_tags_idx": "ON traces USING gin (tags)",
    "insights_evidence_trace_ids_idx": (
        "ON insights USING gin ((evidence -> 'trace_ids') jsonb_path_ops)"
    ),
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
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, definition in INDEXES.items():
            state = _index_is_valid(name)
            if state is True:
                continue
            if state is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY {name} {definition}")
