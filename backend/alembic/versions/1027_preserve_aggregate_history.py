"""Keep permanent evidence of missing aggregate sources; activation is separate.

Revision ID: 1027
Revises: 1026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1027"
down_revision: str | None = "1026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "aggregate_incomplete_hours"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE projects ADD COLUMN aggregate_history_unverified_before timestamptz")
    op.execute("""
        CREATE TABLE aggregate_incomplete_hours (
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            bucket_hour timestamptz NOT NULL,
            reason text NOT NULL
                CHECK (reason IN ('retention', 'erasure', 'correction_unavailable')),
            recorded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, bucket_hour),
            CHECK (bucket_hour = date_trunc('hour', bucket_hour, 'UTC'))
        )
    """)
    _enable_rls(TABLE)


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
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM aggregate_incomplete_hours)
               OR EXISTS (SELECT 1 FROM projects
                          WHERE aggregate_history_unverified_before IS DISTINCT FROM
                                '1970-01-01 00:00:00+00'::timestamptz) THEN
                RAISE EXCEPTION 'Aggregate coverage state is required; retain migration 1027';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE aggregate_incomplete_hours")
    op.execute("ALTER TABLE projects DROP COLUMN aggregate_history_unverified_before")
