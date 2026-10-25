"""Tell the planner how many days the trace histogram groups by.

Revision ID: 1017
Revises: 1016
Create Date: 2026-10-24

`GET /traces/histogram` groups a window's traces by `date_bin('1 day', started_at, <UTC
midnight>)`. Without statistics on that expression Postgres guesses one group per few traces, so
over 30 days it sorts every trace of the window (spilling to disk on a busy project) instead of
hashing ~30 groups: 210 ms median on the 300 000-trace load seed, against the 150 ms budget in
docs/performance.md, and 69 ms with these statistics. The histogram writes the bucket with the
same literal constants (`app/explorer/trace_histogram.py`), which the planner needs to match it.

CREATE STATISTICS takes a SHARE UPDATE EXCLUSIVE lock, which lets reads and writes through; the
migration waits at most 5 seconds for it. ANALYZE then samples the table (it does not rewrite
it) so the statistics apply at once rather than after the next autovacuum analyze.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1017"
down_revision: str | None = "1016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        "CREATE STATISTICS traces_started_day_bin ON "
        "(date_bin('1 day'::interval, started_at, '2000-01-01 00:00:00+00'::timestamptz)) "
        "FROM traces"
    )
    op.execute("ANALYZE traces")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("DROP STATISTICS IF EXISTS traces_started_day_bin")
