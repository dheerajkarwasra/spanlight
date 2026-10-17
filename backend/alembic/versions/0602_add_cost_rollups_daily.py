"""Add the daily cost rollups and `user_stats_daily.computed_at`.

Revision ID: 0602
Revises: 0601
Create Date: 2026-10-15

`cost_rollups_daily` holds one row per project, UTC day, provider, model and service tier, built
from `llm` spans by the daily cost rollup job. The reconciliation report sums it over a billing
source's mapped projects and never reads spans. Tokens are split by billable class (uncached
input, cache reads, 5-minute and 1-hour cache writes, output), and the calls are counted by what
happened to their price: priced, unpriced, usage not reported, cut streams, gateway cache hits
and calls with a pricing modifier Spanlight does not model.

Choices worth knowing about:

* The primary key is a `bigint` identity, as on `span_rollups_hourly`. The natural key is the
  unique expression index `cost_rollups_daily_natural_key`, which reads a NULL provider, model or
  tier as the empty string: NULLs never collide in a plain unique index, so two rows for the same
  unknown model would otherwise both be stored. The rollup job upserts on it.
* `provider` and `model` are kept as the spans stored them; `provider_canonical` and
  `model_pattern` are what the price table matched, NULL when nothing matched. `service_tier`
  takes the values `spans.service_tier` allows.
* Every count and token column is non-negative. `cost_usd` (fees included) and `fee_usd` are NULL
  when no call of the row was priced, never 0.
* Row-level security is ENABLE + FORCE with the project isolation and worker bypass policies in
  the exact form of every project table (0505). `(project_id, day)` serves the report and the
  retention delete.
* `user_stats_daily.computed_at` is a constant-default column add: catalog-only, no rewrite.

Both steps take an ACCESS EXCLUSIVE lock for milliseconds (a new table, a catalog-only column
add); the migration waits at most 5 seconds for it rather than stall the end-user refresh behind
the wait. Downgrade drops the column and the table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0602"
down_revision: str | None = "0601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
TABLE = "cost_rollups_daily"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"

COUNT_COLUMNS = (
    "calls",
    "priced_calls",
    "unpriced_calls",
    "usage_unreported_calls",
    "cut_stream_calls",
    "cache_hit_calls",
    "unmodelled_modifier_calls",
    "uncached_input_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "output_tokens",
    "web_search_requests",
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _create_table()
    _enable_rls()
    op.execute(
        "ALTER TABLE user_stats_daily ADD COLUMN computed_at timestamptz NOT NULL DEFAULT now()"
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE user_stats_daily DROP COLUMN IF EXISTS computed_at")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _create_table() -> None:
    counts = ",\n".join(
        f"            {column} bigint NOT NULL CHECK ({column} >= 0)" for column in COUNT_COLUMNS
    )
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id          uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            day                 date NOT NULL,
            provider            text,
            provider_canonical  text,
            model               text,
            model_pattern       text,
            service_tier        text
                CONSTRAINT cost_rollups_daily_service_tier_check
                CHECK (service_tier IN ('standard', 'batch', 'priority', 'flex')),
{counts},
            cost_usd            numeric(14, 8) CHECK (cost_usd >= 0),
            fee_usd             numeric(14, 8) CHECK (fee_usd >= 0),
            computed_at         timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX cost_rollups_daily_natural_key ON {TABLE} "
        "(project_id, day, coalesce(provider, ''), coalesce(model, ''), "
        "coalesce(service_tier, ''))"
    )
    op.execute(f"CREATE INDEX cost_rollups_daily_project_id_day_idx ON {TABLE} (project_id, day)")


def _enable_rls() -> None:
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY cost_rollups_daily_project_isolation ON {TABLE} "
        f"USING ({PROJECT_EXPRESSION}) WITH CHECK ({PROJECT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY cost_rollups_daily_worker_bypass ON {TABLE} "
        f"USING ({BYPASS_EXPRESSION}) WITH CHECK ({BYPASS_EXPRESSION})"
    )
