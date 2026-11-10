"""Vacuum and analyze `jobs` sooner.

Revision ID: 1046
Revises: 1045
Create Date: 2026-11-10

`jobs` is a queue: every job is inserted, updated at least twice (claimed, finished) and deleted
after seven days. No update can be HOT, because `status` is in the predicates of the two partial
indexes a claim reads (`jobs_queued_run_after_idx`, `jobs_running_lease_idx`), so every update
leaves a dead entry in them. The finished rows kept for a week make the table large compared
with its churn, and the default 20 % threshold then waits for thousands of dead rows: on the
capacity database 20 000 updates over 63 000 rows got two autovacuums and left 7 000 dead rows.

- `autovacuum_vacuum_scale_factor = 0.02` vacuums at 2 % dead rows, so a claim walks few dead
  index entries.
- `autovacuum_analyze_scale_factor = 0.02` keeps the status counts the planner uses current.

`ALTER TABLE ... SET` takes `SHARE UPDATE EXCLUSIVE` and rewrites nothing: claims and inserts go
on. It waits only for an autovacuum of `jobs` in progress; `lock_timeout` makes it fail cleanly
after 5 s instead of queueing every later lock behind it, and the migrate step is then re-run.
The previous application version runs unchanged with these options.

Downgrade resets both options to the server defaults, under the same lock and timeout.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1046"
down_revision: str | None = "1045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE jobs SET "
        "(autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02)"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE jobs RESET (autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor)"
    )
