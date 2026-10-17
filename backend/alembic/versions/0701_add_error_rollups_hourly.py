"""Add the hourly error rollups.

Revision ID: 0701
Revises: 0700
Create Date: 2026-10-16

`error_rollups_hourly` holds one row per project, hour, environment, provider, model, error class
and rate-limit kind, counted over failed `llm` spans by the hourly rollup job, in the same
transaction and range as `span_rollups_hourly`. The provider health page and the error alerts
read it instead of scanning spans beyond 24 hours.

Choices worth knowing about:

* The primary key is a `bigint` identity, as on the other rollup tables. Unknown environment,
  provider, model and rate-limit kind stay NULL (ADR 0006), and the natural key
  `error_rollups_hourly_dimensions_key` is `UNIQUE NULLS NOT DISTINCT` (Postgres 15+), so two
  rows for the same unknown dimensions collide.
* `errors` counts the failed spans; `with_retry_after` the subset of them that carried a
  provider `retry-after`, so it can never exceed `errors` (CHECK). Both are non-negative.
* `error_class` is not checked against a list: the spans CHECK already guards the values the
  rollup copies, and a new class would otherwise need two migrations.
* Row-level security is ENABLE + FORCE with the project isolation and worker bypass policies in
  the exact form of every project table (0505). `(project_id, bucket_start)` serves the reads
  and the retention delete.

A new table takes an ACCESS EXCLUSIVE lock on nothing anyone else uses; the 5 second lock wait
cap covers the foreign key's brief lock on `projects`. Downgrade drops the table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0701"
down_revision: str | None = "0700"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
TABLE = "error_rollups_hourly"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id        uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            bucket_start      timestamptz NOT NULL,
            environment       text,
            provider          text,
            model             text,
            error_class       text NOT NULL,
            rate_limit_kind   text,
            errors            bigint NOT NULL CHECK (errors >= 0),
            with_retry_after  bigint NOT NULL CHECK (with_retry_after >= 0),
            computed_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT {TABLE}_with_retry_after_le_errors_check CHECK (with_retry_after <= errors),
            CONSTRAINT {TABLE}_dimensions_key UNIQUE NULLS NOT DISTINCT
                (project_id, bucket_start, environment, provider, model, error_class,
                 rate_limit_kind)
        )
        """
    )
    op.execute(f"CREATE INDEX {TABLE}_project_bucket_idx ON {TABLE} (project_id, bucket_start)")
    _enable_rls()


def _enable_rls() -> None:
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
