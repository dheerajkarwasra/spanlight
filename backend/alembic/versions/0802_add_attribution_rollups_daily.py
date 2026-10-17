"""Add `attribution_rollups_daily`, the daily cost and usage of every group of traces.

Revision ID: 0802
Revises: 0801
Create Date: 2026-10-16

The attribution view answers "what did each customer, feature or trace name cost" over windows
longer than a day from this table instead of the raw traces. The attribution refresh job writes
one row per project, UTC day, environment, group kind, key and value: a promoted metadata key
(`metadata`, with the key in `group_key`), the trace's name (`trace_name`), its end user (`user`)
or its release (`release`).

Choices worth knowing about:

* `bucket` says what a row stands for: one real value (`value`, with `group_value` set), the
  traces without the dimension (`none`), or the fold of the values past the cardinality cap
  (`other`, with `folded_values` counting them). The three CHECKs tie `group_key`,
  `group_value` and `folded_values` to the kind and bucket, so no row can be ambiguous.
* The natural key is `UNIQUE NULLS NOT DISTINCT`: the environment, the key and the value are
  NULL for some rows, and a plain unique key never lets NULLs collide, so a second refresh of the
  traces without an environment would add a row instead of updating it. The refresh upserts on
  this key, and the attribution read is a range scan on it (project, kind, key, then days).
  `project_id` leads it, so the foreign key needs no index of its own.
* Every count and token column is non-negative and `NOT NULL` (an unknown token count is 0, the
  rollup convention). `cost_usd` (fees included), `retries` and `retry_cost_usd` are NULL when
  nothing of the row was priced or reported, never 0.
* Row-level security is ENABLE + FORCE with the project isolation and worker bypass policies in
  the exact form of every project table (0505), each setting read once per statement through
  `(SELECT ...)` (the InitPlan form of 0017 and 0404).

The migration creates a new table, which takes a lock on `projects` for its foreign key; it waits
at most 5 seconds for it. Downgrade drops the table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0802"
down_revision: str | None = "0801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
TABLE = "attribution_rollups_daily"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"
COUNT_COLUMNS = ("llm_calls", "errors", "unpriced_calls", "input_tokens", "output_tokens")


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _create_table()
    _enable_rls()


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _create_table() -> None:
    counts = ",\n".join(
        f"            {column} bigint NOT NULL CHECK ({column} >= 0)" for column in COUNT_COLUMNS
    )
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id      uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            day             date NOT NULL,
            environment     text,
            group_kind      text NOT NULL
                CONSTRAINT {TABLE}_group_kind_check
                CHECK (group_kind IN ('metadata', 'trace_name', 'user', 'release')),
            group_key       text,
            bucket          text NOT NULL
                CONSTRAINT {TABLE}_bucket_check CHECK (bucket IN ('value', 'none', 'other')),
            group_value     text,
            traces          integer NOT NULL CHECK (traces >= 0),
{counts},
            cost_usd        numeric(14, 8) CHECK (cost_usd >= 0),
            retries         bigint CHECK (retries >= 0),
            retry_cost_usd  numeric(14, 8) CHECK (retry_cost_usd >= 0),
            folded_values   integer CHECK (folded_values >= 0),
            computed_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT {TABLE}_natural_key UNIQUE NULLS NOT DISTINCT
                (project_id, group_kind, group_key, environment, day, bucket, group_value),
            CONSTRAINT {TABLE}_key_for_kind
                CHECK ((group_kind = 'metadata') = (group_key IS NOT NULL)),
            CONSTRAINT {TABLE}_value_for_bucket
                CHECK ((bucket = 'value') = (group_value IS NOT NULL)),
            CONSTRAINT {TABLE}_folded_for_bucket
                CHECK ((bucket = 'other') = (folded_values IS NOT NULL))
        )
        """
    )


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
