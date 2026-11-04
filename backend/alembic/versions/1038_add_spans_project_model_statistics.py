"""Tell the planner how a project's spans spread over models.

Revision ID: 1038
Revises: 1036
Create Date: 2026-11-02

The trace list's model filter is an `EXISTS` over the trace's spans with `spans.model`. Postgres
estimates `project_id = … AND model = …` by multiplying the project's share of the table by the
model's share across every project, so on a shared table a busy project's most common model looks
rare: on the 100 000-span load seed beside a 1 000 000-span project it expected 901 of 19 315
spans, hashed every span of that model the project ever stored, and took 30 ms for a 7- or 30-day
window. Statistics on the pair (`mcv`) give the planner the real share; with the filter's bound on
the spans' start (`app.explorer.trace_queries.has_span`) the same pages take 1 to 4 ms.

CREATE STATISTICS takes a SHARE UPDATE EXCLUSIVE lock, which lets reads and writes through; the
migration waits at most 5 seconds for it. ANALYZE then samples the table (it does not rewrite
it) so the statistics apply at once rather than after the next autovacuum analyze. Ingestion does
no extra work: extended statistics are computed only by ANALYZE.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1038"
down_revision: str | None = "1036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("CREATE STATISTICS spans_project_model (mcv) ON project_id, model FROM spans")
    op.execute("ANALYZE spans")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("DROP STATISTICS IF EXISTS spans_project_model")
