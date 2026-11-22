"""Add run_reservations table for per-run gateway circuit breakers.

Revision ID: 1048
Revises: 1047
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1048"
down_revision: str | None = "1047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_reservations (
            project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            trace_id     text NOT NULL,
            step_count   integer NOT NULL DEFAULT 0,
            reserved_usd numeric(18, 8) NOT NULL DEFAULT 0,
            settled_usd  numeric(18, 8) NOT NULL DEFAULT 0,
            expires_at   timestamptz NOT NULL,
            PRIMARY KEY (project_id, trace_id)
        )
        """
    )
    op.execute("CREATE INDEX run_reservations_expires_idx ON run_reservations (expires_at)")
    op.execute("ALTER TABLE run_reservations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE run_reservations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY run_reservations_project_isolation ON run_reservations "
        f"USING ({PROJECT_EXPRESSION}) WITH CHECK ({PROJECT_EXPRESSION})"
    )
    op.execute(
        "CREATE POLICY run_reservations_worker_bypass ON run_reservations "
        f"USING ({BYPASS_EXPRESSION}) WITH CHECK ({BYPASS_EXPRESSION})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_reservations")
