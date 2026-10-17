"""Add the telemetry loss counters: hourly losses per project and each SDK exporter's totals.

Revision ID: 0703
Revises: 0702
Create Date: 2026-10-16

`telemetry_losses_hourly` counts the spans a project lost on the way in, per hour, source
(`gateway`, `ingest` or `sdk`) and reason, so the completeness page can say how much of the
picture is missing instead of showing a partial one as whole. `sdk_exporter_counters` keeps the
cumulative `dropped_total` and `failed_total` each SDK exporter process reports with its batches;
the loss flush turns the increase between two reports into `sdk` losses.

Choices worth knowing about:

* `UNIQUE (project_id, bucket_start, source, reason)` is the natural key the flush upserts on
  (adding to `count`); it also serves the project's window reads and the retention delete.
  `count`, `dropped_total` and `failed_total` are never negative.
* `reason` is not checked against a list: the vocabulary is the application's and grows with it.
* `sdk_exporter_counters` is keyed by `(project_id, exporter_id)`: the exporter id is 32
  lowercase hex characters the SDK draws once per process, so one process reporting to two
  projects keeps two rows. `sdk_version` is at most 32 characters.
* Both tables are under row-level security, ENABLE + FORCE with the project isolation and worker
  bypass policies in the exact form of every project table (0505).

Two new tables in one transaction, under the 5 second lock wait cap. Downgrade drops both.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0703"
down_revision: str | None = "0702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
LOSSES = "telemetry_losses_hourly"
COUNTERS = "sdk_exporter_counters"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        f"""
        CREATE TABLE {LOSSES} (
            id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            bucket_start  timestamptz NOT NULL,
            source        text NOT NULL
                CONSTRAINT {LOSSES}_source_check CHECK (source IN ('gateway', 'ingest', 'sdk')),
            reason        text NOT NULL,
            count         bigint NOT NULL CONSTRAINT {LOSSES}_count_check CHECK (count >= 0),
            CONSTRAINT {LOSSES}_key UNIQUE (project_id, bucket_start, source, reason)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {COUNTERS} (
            project_id     uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            exporter_id    text NOT NULL
                CONSTRAINT {COUNTERS}_exporter_id_check CHECK (exporter_id ~ '^[0-9a-f]{{32}}$'),
            dropped_total  bigint NOT NULL
                CONSTRAINT {COUNTERS}_dropped_total_check CHECK (dropped_total >= 0),
            failed_total   bigint NOT NULL
                CONSTRAINT {COUNTERS}_failed_total_check CHECK (failed_total >= 0),
            sdk            text NOT NULL,
            sdk_version    text NOT NULL
                CONSTRAINT {COUNTERS}_sdk_version_check CHECK (char_length(sdk_version) <= 32),
            last_seen_at   timestamptz NOT NULL,
            PRIMARY KEY (project_id, exporter_id)
        )
        """
    )
    _enable_rls(LOSSES)
    _enable_rls(COUNTERS)


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
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f"DROP TABLE IF EXISTS {COUNTERS}")
    op.execute(f"DROP TABLE IF EXISTS {LOSSES}")
