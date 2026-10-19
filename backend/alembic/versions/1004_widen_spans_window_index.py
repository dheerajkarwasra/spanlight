"""Carry `usage_rollup` in the covering index of windowed span reads.

Revision ID: 1004
Revises: 1003
Create Date: 2026-10-18

Migration 0900 added `spans.usage_rollup`, and the raw overview, time series and per-model reads
filter their token and cost sums on it. `spans_project_started_cov_idx` (0016) does not carry
the column, so those reads can no longer be answered from the index: the planner fetches every
row of the window from the table, through a bitmap heap scan on another index. The Bet 4 spec
widens the index once a load test shows that heap fetch; the 2026-10-18 read-path test did. On
the load database a 24 hour overview of a project with 140 000 spans in the window went from
76 ms to 33 ms, and one with 13 000 spans from 25 ms to 4 ms, both as index-only scans.

The index is replaced, not altered: Postgres cannot add an `INCLUDE` column in place. The new
index, `spans_project_started_window_idx`, has the same keys `(project_id, started_at)` and the
0016 columns plus `usage_rollup` (one byte). It is built `CONCURRENTLY` before the old one is
dropped the same way, so writes continue and a read always has an index; both steps run in
`autocommit_block()` and skip or clean up what an interrupted run left, as in 0016. The build
takes a while and extra disk on a big `spans` table.

Downgrade builds the 0016 index again and drops this one.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1004"
down_revision: str | None = "1003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WINDOW_INDEX = "spans_project_started_window_idx"
PREVIOUS_INDEX = "spans_project_started_cov_idx"

# The 0016 columns, unchanged.
PREVIOUS_COLUMNS = (
    "trace_id",
    "kind",
    "status",
    "duration_ms",
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "provider",
    "model",
    "time_to_first_token_ms",
)
INCLUDED_COLUMNS = (*PREVIOUS_COLUMNS, "usage_rollup")


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


def _build_index(name: str, columns: tuple[str, ...]) -> None:
    """Create `name` concurrently, unless a valid one is already there (see 0016)."""
    state = _index_is_valid(name)
    if state is True:
        return
    if state is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    included = ", ".join(columns)
    op.execute(
        f"CREATE INDEX CONCURRENTLY {name} ON spans (project_id, started_at) INCLUDE ({included})"
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _build_index(WINDOW_INDEX, INCLUDED_COLUMNS)
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {PREVIOUS_INDEX}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _build_index(PREVIOUS_INDEX, PREVIOUS_COLUMNS)
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {WINDOW_INDEX}")
