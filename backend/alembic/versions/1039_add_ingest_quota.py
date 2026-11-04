"""Per-project ingest caps: three override columns on `projects` and `ingest_quota_days`.

Revision ID: 1039
Revises: 1038
Create Date: 2026-11-02

The hosted service shares one Postgres between tenants, and nothing bounded what one project
stored in a day. The instance settings `INGEST_SPANS_PER_SECOND`, `INGEST_SPANS_PER_DAY` and
`INGEST_BYTES_PER_DAY` cap every project; the three nullable columns here override them for one
project (`NULL` is the instance default). `0` is refused: unlimited for one project is a very
large number, so an operator cannot make a project unbounded by accident.

`ingest_quota_days` holds one row per project and UTC day: the spans the write inserted and the
request body bytes it admitted. The ingest route reads today's row before the pipeline and adds
to it as the last statement before commit; the cleanup job prunes rows older than 35 days.
Under row-level security, ENABLE + FORCE with the project isolation and worker bypass policies
in the exact form of every project table (0505).

The `ALTER TABLE projects` takes `ACCESS EXCLUSIVE` on `projects` for the time it takes to add
three nullable columns and validate three CHECKs on a small table; no rewrite. Every in-flight
batch holds `FOR KEY SHARE` on its project row for the whole write, so the ALTER queues behind
them and new batches queue behind the ALTER: run it off-peak, and on a lock timeout re-run
`spanlight migrate`. Downgrade drops the table (a counter a day resets) and the three columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1039"
down_revision: str | None = "1038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
TABLE = "ingest_quota_days"
OVERRIDES = (
    ("ingest_spans_per_second", "integer"),
    ("ingest_spans_per_day", "bigint"),
    ("ingest_bytes_per_day", "bigint"),
)
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    additions = ", ".join(
        f"ADD COLUMN {column} {kind} CONSTRAINT projects_{column}_check CHECK ({column} > 0)"
        for column, kind in OVERRIDES
    )
    op.execute(f"ALTER TABLE projects {additions}")
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            project_id  uuid   NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            day         date   NOT NULL,
            spans       bigint NOT NULL DEFAULT 0 CONSTRAINT {TABLE}_spans_check CHECK (spans >= 0),
            bytes       bigint NOT NULL DEFAULT 0 CONSTRAINT {TABLE}_bytes_check CHECK (bytes >= 0),
            PRIMARY KEY (project_id, day)
        )
        """
    )
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


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
    drops = ", ".join(f"DROP COLUMN IF EXISTS {column}" for column, _ in OVERRIDES)
    op.execute(f"ALTER TABLE projects {drops}")
