"""Leave room on `traces` pages for in-place updates, and vacuum the table sooner.

Revision ID: 1005
Revises: 1004
Create Date: 2026-10-18

Every ingest batch updates each of its traces twice: the upsert, then the recompute of the totals
from the stored spans. With the default `fillfactor` of 100 a page has no free space for the new
row version, so most updates land on another page and touch every index on `traces` instead of
being HOT (heap-only). On the load database only 28.9 % of 1.27 M trace updates were HOT, and the
table carried 36 000 dead tuples.

- `fillfactor = 70` keeps 30 % of each page free, so the new version of a row usually fits on the
  same page and the update is HOT (no index entry is written).
- `autovacuum_vacuum_scale_factor = 0.02` vacuums once 2 % of the rows are dead instead of the
  default 20 %, so the dead versions are reclaimed and the free space is there for the next update.
- `autovacuum_analyze_scale_factor = 0.01` keeps the planner statistics current on the same table.

`spans` is left alone: it is append-mostly, and a lower fillfactor would cost disk on the largest
table for no measured update problem.

`ALTER TABLE ... SET` takes `SHARE UPDATE EXCLUSIVE` and rewrites nothing: reads and writes go on.
It waits only for an autovacuum of `traces` in progress; `lock_timeout` makes it fail cleanly
after 5 s instead of queueing every later lock behind it, and the migrate step is then re-run.
`fillfactor` applies to pages written from now on; existing pages gain room as vacuum reclaims
their dead tuples.

Downgrade resets the three options to the server defaults, under the same lock and timeout.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1005"
down_revision: str | None = "1004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE traces SET (fillfactor = 70, "
        "autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.01)"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE traces RESET "
        "(fillfactor, autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor)"
    )
