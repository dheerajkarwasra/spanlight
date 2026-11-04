"""Keep deleted current-month spend for budget admission instead of failing closed.

Revision ID: 1031
Revises: 1030
Create Date: 2026-11-01

Migration 1026 made a block budget refuse every call for the rest of the UTC period once any
priced span of the project was deleted (retention, an end-user erasure, an operator), and
stamped every existing project that way at upgrade. Deleted spend is now kept instead:

* `budget_deleted_spans` holds one row per deleted priced span of the database's current UTC
  month: ids, start time, cost, model and gateway key; no user identifier, trace name, metadata
  or payload. Admission (`app.budgets.history`) adds these rows to the period's spend, so a
  deletion neither hides spend nor blocks the project. The worker prunes the rows with the budget
  counters (62 days). Row-level security applies as on `budget_counters`; the app role's
  privileges come from the default privileges `ensure-app-role` set (1022 grants nothing either).
* The 1026 trigger function `mark_missing_budget_history` keeps its name (the trigger is
  unchanged) and now writes the ledger instead of the project marker. Spans of an earlier month
  cannot be in any current period, so they are not kept. A span of a project being deleted is
  skipped: its row would cascade away anyway, and the parent row may already be gone. Correlated
  calls need no special case: their reservation keeps their charge, and the history read leaves
  ledger rows that a reservation or a stored span already covers out.
* `projects.budget_history_missing_through` is re-derived from evidence of this month, replacing
  1026's blanket `now()`: the moment retention could have deleted a span of this month
  (`now() - retention_days`, when that is inside the month), an `end_user.erase` or
  `end_user.erase_request` audit event of the project this month, and a `project.update` event
  of the project this month whose `changes` metadata names `retention_days` (the route writes
  `{"changes": {<field>: <value>}}`, so an earlier, shorter retention is unknown). A project
  with none of these starts with a known period. The marker is only ever set here again.

Downgrade drops the ledger (its spend is then unknown to the old rule, which fails closed only
on the next deletion) and restores the 1026 function body; a later upgrade re-derives the marker.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1031"
down_revision: str | None = "1030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "budget_deleted_spans"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"

LEDGER_FUNCTION = """
    CREATE OR REPLACE FUNCTION mark_missing_budget_history() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        INSERT INTO budget_deleted_spans
               (project_id, trace_id, span_id, started_at, cost_usd, model, source_key_id)
        SELECT d.project_id, d.trace_id, d.span_id, d.started_at, d.cost_usd, d.model,
               d.source_key_id
        FROM deleted_spans d
        WHERE NOT d.usage_rollup AND d.cost_usd > 0
          AND d.started_at >= date_trunc('month', now(), 'UTC')
          AND EXISTS (SELECT 1 FROM projects p WHERE p.id = d.project_id)
        ON CONFLICT (project_id, trace_id, span_id)
        DO UPDATE SET cost_usd = excluded.cost_usd, started_at = excluded.started_at,
                      model = excluded.model, source_key_id = excluded.source_key_id,
                      deleted_at = now();
        RETURN NULL;
    END $$
"""

# 1026's body, verbatim.
MARKER_FUNCTION = """
    CREATE OR REPLACE FUNCTION mark_missing_budget_history() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        UPDATE projects p
        SET budget_history_missing_through = greatest(p.budget_history_missing_through,
                                                      lost.last_started)
        FROM (SELECT d.project_id,
                     max(greatest(d.started_at, r.expires_at - interval '12 minutes'))
                         AS last_started
              FROM deleted_spans d
              LEFT JOIN budget_reservations r
                ON r.project_id = d.project_id AND r.trace_id = d.trace_id
               AND r.span_id = d.span_id
              WHERE (NOT d.usage_rollup AND d.cost_usd > 0) OR r.charged_usd > 0
              GROUP BY d.project_id) lost
        WHERE p.id = lost.project_id;
        RETURN NULL;
    END $$
"""

REDERIVE_MARKER = """
    WITH clock AS (SELECT now() AS t, date_trunc('month', now(), 'UTC') AS m)
    UPDATE projects p SET budget_history_missing_through = (
        SELECT max(x) FROM (VALUES
            (CASE WHEN c.t - make_interval(days => p.retention_days) > c.m
                  THEN c.t - make_interval(days => p.retention_days) END),
            ((SELECT c.t FROM audit_events a WHERE a.project_id = p.id
                AND a.action IN ('end_user.erase', 'end_user.erase_request')
                AND a.created_at >= c.m LIMIT 1)),
            ((SELECT c.t FROM audit_events a WHERE a.project_id = p.id
                AND a.action = 'project.update' AND a.metadata -> 'changes' ? 'retention_days'
                AND a.created_at >= c.m LIMIT 1))
        ) AS v(x))
    FROM clock c
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            project_id    uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            trace_id      text NOT NULL,
            span_id       text NOT NULL,
            started_at    timestamptz NOT NULL,
            cost_usd      numeric(18, 8) NOT NULL CHECK (cost_usd > 0),
            model         text,
            source_key_id uuid,
            deleted_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, trace_id, span_id)
        )
        """
    )
    op.execute(f"CREATE INDEX {TABLE}_started_idx ON {TABLE} (project_id, started_at)")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {TABLE}_project_isolation ON {TABLE} "
        f"USING ({PROJECT_EXPRESSION}) WITH CHECK ({PROJECT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY {TABLE}_worker_bypass ON {TABLE} "
        f"USING ({BYPASS_EXPRESSION}) WITH CHECK ({BYPASS_EXPRESSION})"
    )
    op.execute(LEDGER_FUNCTION)
    op.execute(REDERIVE_MARKER)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(MARKER_FUNCTION)
    op.execute(f"DROP TABLE {TABLE}")
