"""Add sharded aggregate correction obligations without changing existing writers.

Revision ID: 1040
Revises: 1039
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1040"
down_revision: str | None = "1039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE aggregate_dirty_shards (
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            bucket_hour timestamptz NOT NULL,
            shard smallint NOT NULL CHECK (shard BETWEEN 0 AND 15),
            generation bigint NOT NULL CHECK (generation > 0),
            first_marked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            pending_consumers smallint NOT NULL DEFAULT 15
                CHECK (pending_consumers BETWEEN 0 AND 15),
            PRIMARY KEY (project_id, bucket_hour, shard),
            CHECK (bucket_hour = date_trunc('hour', bucket_hour, 'UTC'))
        )
    """)
    op.execute(
        "CREATE INDEX aggregate_dirty_shards_oldest ON aggregate_dirty_shards "
        "(first_marked_at, project_id, bucket_hour)"
    )
    op.execute("ALTER TABLE aggregate_dirty_shards ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aggregate_dirty_shards FORCE ROW LEVEL SECURITY")
    project = "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
    bypass = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"
    op.execute(
        f"CREATE POLICY aggregate_dirty_shards_project_isolation ON aggregate_dirty_shards "
        f"USING ({project}) WITH CHECK ({project})"
    )
    op.execute(
        f"CREATE POLICY aggregate_dirty_shards_worker_bypass ON aggregate_dirty_shards "
        f"USING ({bypass}) WITH CHECK ({bypass})"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE aggregate_dirty_shards IN ACCESS EXCLUSIVE MODE")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM aggregate_dirty_shards) THEN
                RAISE EXCEPTION 'Aggregate dirty shard work remains; retain migration 1040';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE aggregate_dirty_shards")
