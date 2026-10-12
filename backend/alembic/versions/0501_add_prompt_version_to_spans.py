"""Link spans to the prompt version that produced them.

Revision ID: 0501
Revises: 0500
Create Date: 2026-10-10

An SDK that fetched a prompt version sends its id with the span, so the dashboard can show which
traces used a version and what that version costs. The column is a plain uuid without a foreign
key: an SDK may send an id the server has not seen, or one whose prompt was deleted since, and
the span must still be stored as sent.

`spans_project_prompt_version_idx (project_id, prompt_version_id, started_at DESC)` answers
"the spans of this version, newest first". Choices worth knowing about:

* The index is partial, `WHERE prompt_version_id IS NOT NULL`: most spans carry no version, and
  leaving them out keeps the index small and cheap to maintain on every ingest.
* Adding a nullable column without a default is a catalog change only; no rows are rewritten.
  It still needs a brief ACCESS EXCLUSIVE lock, so, as in 0400, it runs with a 5 second
  `lock_timeout` outside the migration's transaction.
* The index is built `CONCURRENTLY`, as 0302 builds its own, so ingestion keeps writing to
  `spans` during the build. That cannot run inside a transaction, hence `autocommit_block()`.
  An interrupted build leaves an INVALID index behind; the leftover is dropped first, so running
  the migration again is safe.

Downgrade drops the index (concurrently), then the column. Links already stored are lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0501"
down_revision: str | None = "0500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "spans_project_prompt_version_idx"
LOCK_TIMEOUT = "5s"


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
        # The ALTER needs ACCESS EXCLUSIVE on `spans`: give up rather than queue every ingest
        # write behind a long transaction.
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE spans ADD COLUMN IF NOT EXISTS prompt_version_id uuid")
        op.execute("RESET lock_timeout")
        state = _index_is_valid(INDEX)
        if state is True:
            return
        if state is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX} "
            "ON spans (project_id, prompt_version_id, started_at DESC) "
            "WHERE prompt_version_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE spans DROP COLUMN IF EXISTS prompt_version_id")
        op.execute("RESET lock_timeout")
