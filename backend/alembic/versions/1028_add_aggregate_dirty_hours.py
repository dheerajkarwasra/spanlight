"""Track durable aggregate correction obligations independently of source coverage.

Revision ID: 1028
Revises: 1027
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1028"
down_revision: str | None = "1027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("CREATE SEQUENCE aggregate_dirty_generation_seq AS bigint NO CYCLE")
    op.execute("""
        CREATE TABLE aggregate_dirty_hours (
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            bucket_hour timestamptz NOT NULL,
            generation bigint NOT NULL CHECK (generation > 0),
            first_marked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            pending_consumers smallint NOT NULL DEFAULT 15
                CHECK (pending_consumers BETWEEN 0 AND 15),
            PRIMARY KEY (project_id, bucket_hour),
            CHECK (bucket_hour = date_trunc('hour', bucket_hour, 'UTC'))
        )
    """)
    op.execute(
        "CREATE INDEX aggregate_dirty_hours_oldest ON aggregate_dirty_hours "
        "(first_marked_at, project_id, bucket_hour)"
    )
    op.execute("ALTER TABLE aggregate_dirty_hours ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aggregate_dirty_hours FORCE ROW LEVEL SECURITY")
    project = "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
    bypass = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"
    op.execute(
        f"CREATE POLICY aggregate_dirty_hours_project_isolation ON aggregate_dirty_hours "
        f"USING ({project}) WITH CHECK ({project})"
    )
    op.execute(
        f"CREATE POLICY aggregate_dirty_hours_worker_bypass ON aggregate_dirty_hours "
        f"USING ({bypass}) WITH CHECK ({bypass})"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM aggregate_dirty_hours) THEN
                RAISE EXCEPTION 'Aggregate dirty work remains; retain migration 1028';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE aggregate_dirty_hours")
    op.execute("DROP SEQUENCE aggregate_dirty_generation_seq")
