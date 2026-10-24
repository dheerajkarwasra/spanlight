"""Keep the per-user daily stats per environment.

Revision ID: 1015
Revises: 1014
Create Date: 2026-10-21

`user_stats_daily` gains `environment`, so the Users list and detail can narrow to one
environment like every other view. A row is now one project, UTC day, environment and end user.

Choices worth knowing about:

* Unknown environments are NULL, as in `attribution_rollups_daily`; an empty string is a real
  environment (`environment=` names it on every route), so it cannot stand for "none". A primary
  key cannot hold NULL, so the primary key becomes the natural key `UNIQUE NULLS NOT DISTINCT
  (project_id, day, environment, external_user_id)`: a second refresh of the traces without an
  environment updates their row instead of adding one. Its leading columns are the old key's, so
  the list's range scan and retention's delete by day keep their index; the detail keeps
  `user_stats_daily_project_user_day_idx`.
* The column is added nullable without a default, a catalog-only change. Swapping the key builds
  one unique index under the table lock; the table holds one small row per user and day, bounded
  by trace retention, and the migration waits at most 5 seconds for the lock.
* The rows are derived data, so every (project, day) that still has user-tagged traces is
  re-derived here per environment, with the refresh job's figures (LLM spans of the day's traces;
  usage rollups left out of the unpriced count, the tokens and the cost). It is one set-based
  statement bounded by retention, run past row-level security. A day whose traces are all gone
  keeps its rows, under no environment, so no history is lost.
* While the old app version still runs during a deploy, its refresh upsert names the old key and
  fails for that pass (the job has a single attempt; the next pass after the deploy writes the
  day), and the old API's day series may show a multi-environment user's day as several entries
  until the new API is live. On a large install the backfill can take minutes, holding this
  table's lock (Users reads wait; ingestion does not).

Downgrade folds every environment of a (project, day, user) back into one row, drops the column
and restores the primary key.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1015"
down_revision: str | None = "1014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
TABLE = "user_stats_daily"
NATURAL_KEY = f"{TABLE}_natural_key"
FIGURES = "traces, llm_calls, errors, unpriced_calls, cost_usd, tokens, computed_at"

# The refresh statement of `app.end_users.refresh`, per environment, over every (project, day)
# that has rows. The fresh figures go to a temporary table first, so the delete and the insert
# are separate statements and never race on the natural key.
_REDERIVE = f"""
    CREATE TEMPORARY TABLE user_stats_rederived ON COMMIT DROP AS
    WITH days AS (
        SELECT DISTINCT project_id, day, (day::timestamp AT TIME ZONE 'UTC') AS start
        FROM {TABLE}
    )
    SELECT t.project_id,
           days.day,
           t.environment,
           t.external_user_id,
           count(DISTINCT t.trace_id)                                    AS traces,
           count(s.span_id)                                              AS llm_calls,
           count(s.span_id) FILTER (WHERE s.status = 'error')            AS errors,
           count(s.span_id) FILTER (WHERE s.cost_usd IS NULL AND NOT s.usage_rollup)
                                                                         AS unpriced_calls,
           sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)             AS cost_usd,
           coalesce(sum(coalesce(s.input_tokens, 0) + coalesce(s.output_tokens, 0))
                        FILTER (WHERE NOT s.usage_rollup), 0)            AS tokens
    FROM traces AS t
    JOIN days
      ON days.project_id = t.project_id
     AND t.started_at >= days.start
     AND t.started_at < days.start + interval '1 day'
    LEFT JOIN spans AS s
           ON s.project_id = t.project_id
          AND s.trace_id = t.trace_id
          AND s.kind = 'llm'
          AND s.started_at >= days.start
    WHERE t.external_user_id IS NOT NULL
    GROUP BY 1, 2, 3, 4;

    DELETE FROM {TABLE} AS u
    USING (SELECT DISTINCT project_id, day FROM user_stats_rederived) AS covered
    WHERE u.project_id = covered.project_id AND u.day = covered.day;

    INSERT INTO {TABLE} (project_id, day, environment, external_user_id, {FIGURES})
    SELECT project_id, day, environment, external_user_id, traces, llm_calls, errors,
           unpriced_calls, cost_usd, tokens, now()
    FROM user_stats_rederived;
"""

# Every environment of a (project, day, user) summed back into one row.
_COLLAPSE = f"""
    CREATE TEMPORARY TABLE user_stats_folded ON COMMIT DROP AS
    SELECT project_id, day, external_user_id, sum(traces) AS traces,
           sum(llm_calls) AS llm_calls, sum(errors) AS errors,
           sum(unpriced_calls) AS unpriced_calls, sum(cost_usd) AS cost_usd,
           sum(tokens) AS tokens, max(computed_at) AS computed_at
    FROM {TABLE}
    GROUP BY project_id, day, external_user_id;

    DELETE FROM {TABLE};

    INSERT INTO {TABLE} (project_id, day, external_user_id, {FIGURES})
    SELECT project_id, day, external_user_id, {FIGURES} FROM user_stats_folded;
"""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN environment text")
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {TABLE}_pkey")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {NATURAL_KEY} UNIQUE NULLS NOT DISTINCT "
        "(project_id, day, environment, external_user_id)"
    )
    _with_bypass(_REDERIVE)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _with_bypass(_COLLAPSE)
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {NATURAL_KEY}")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN environment")
    op.execute(f"ALTER TABLE {TABLE} ADD PRIMARY KEY (project_id, day, external_user_id)")


# Copied on purpose from 1006: migrations do not import app code or each other.
def _with_bypass(statement: str) -> None:
    """Run `statement` in one `DO` block with the worker bypass set for its transaction only (the
    table forces row-level security even on its owner)."""
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            {statement}
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )
