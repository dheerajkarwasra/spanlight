"""Add budget counters and reservations: the gateway reserves spend before forwarding a call.

Revision ID: 1022
Revises: 1021
Create Date: 2026-10-25

A `block` budget used to refuse gateway calls only once its alert rule had measured the spend
(every 60 s), so any number of concurrent calls passed in between. The gateway now reserves an
estimate of each call's cost against every matching block budget before the upstream call and
settles the real cost when the span is written (ADR 0017).

* `budget_counters` holds, per budget and period, the gateway spend settled this period and the
  reservations in flight. One hot row per (budget, period): `fillfactor = 70` and no index on
  `updated_at` keep the updates in-page (HOT), as `rate_limit_buckets` does. It is logged, not
  unlogged: a crash must not forget reservations of real money.
* `budget_reservations` holds one row per (call, budget) while the call is out. `held` rows
  are settled by the span write; a row past `expires_at` is marked `reaped` by the worker and
  counted at its estimate until a late settle corrects it. The partial index on `expires_at`
  serves the reaper; `(project_id, trace_id, span_id)` serves the settle.
* Both carry `project_id` and the row-level security policy pair every project-scoped table has
  (the InitPlan form of 0017), and cascade from `budgets` and `projects`.

Expand-only: two new tables in one transaction. Downgrade drops both, forgetting the
reservations in flight (a call already admitted is still settled into its span).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1022"
down_revision: str | None = "1021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("budget_counters", "budget_reservations")

PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE budget_counters (
            budget_id    uuid NOT NULL REFERENCES budgets (id) ON DELETE CASCADE,
            period_start timestamptz NOT NULL,
            project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            settled_usd  numeric(18, 8) NOT NULL DEFAULT 0 CHECK (settled_usd >= 0),
            reserved_usd numeric(18, 8) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (budget_id, period_start)
        ) WITH (fillfactor = 70)
        """
    )
    op.execute("CREATE INDEX budget_counters_project_idx ON budget_counters (project_id)")
    op.execute(
        """
        CREATE TABLE budget_reservations (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            budget_id    uuid NOT NULL REFERENCES budgets (id) ON DELETE CASCADE,
            period_start timestamptz NOT NULL,
            trace_id     text NOT NULL,
            span_id      text NOT NULL,
            amount_usd   numeric(18, 8) NOT NULL CHECK (amount_usd > 0),
            status       text NOT NULL DEFAULT 'held' CHECK (status IN ('held', 'reaped')),
            expires_at   timestamptz NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX budget_reservations_expires_idx ON budget_reservations (expires_at) "
        "WHERE status = 'held'"
    )
    op.execute(
        "CREATE INDEX budget_reservations_call_idx "
        "ON budget_reservations (project_id, trace_id, span_id)"
    )
    op.execute("CREATE INDEX budget_reservations_budget_idx ON budget_reservations (budget_id)")
    for table in TABLES:
        _enable_rls(table)


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_project_isolation ON {table} "
        f"USING ({PROJECT_EXPRESSION}) WITH CHECK ({PROJECT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY {table}_worker_bypass ON {table} "
        f"USING ({BYPASS_EXPRESSION}) WITH CHECK ({BYPASS_EXPRESSION})"
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
