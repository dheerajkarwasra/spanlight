"""Let the token totals of the hourly, attribution and per-user rollups be unknown.

Revision ID: 1043
Revises: 1042
Create Date: 2026-11-04

`span_rollups_hourly.input_tokens`, `output_tokens` and `cached_tokens`, the
`attribution_rollups_daily` `input_tokens` and `output_tokens`, and `user_stats_daily.tokens` were
`NOT NULL`, and their writers coalesced an empty sum to 0. An hour, a group or a user's day none
of whose calls reported usage (a failed call, say) then read "0 tokens" on the overview, the
models table, the chart, attribution and the user pages, where the project rule is that an
unknown value is NULL. Migration 1016 made the same change for `traces`. Each total is now the
sum of the usage the spans reported (usage rollups left out, as before), and NULL when none of
them reported it.

Choices worth knowing about:

* Dropping NOT NULL changes only the catalog; each ALTER takes an ACCESS EXCLUSIVE lock for
  milliseconds, and the migration waits at most 5 seconds for it (`lock_timeout`) rather than
  stall the rollup writers queued behind the wait. The `>= 0` checks stay (NULL passes them).
* The stored zeros of `span_rollups_hourly` and `user_stats_daily` that stand for "no span
  reported usage" are set to NULL here, each row checked against its own spans with the rule the
  writers now apply, and only in hours and days whose source coverage is verified and complete:
  the project's `aggregate_history_unverified_before` lies at or before the bucket and no
  `aggregate_incomplete_hours` row falls in it (the rule of `complete_ranges`, which every rollup
  writer applies), checked in the update's own snapshot. Elsewhere the spans cannot prove that a
  0 was unreported (an embedding call reports 0 output tokens; retention may have taken the one
  span of two that reported it), and a possibly true 0 is not turned into an unknown. So an
  install that has not run `spanlight activate-aggregate-coverage`, or that had projects before
  migration 1027 and keeps their earlier history unverified after it, keeps its old zeros there:
  readers can still show them, marked as a retained snapshot, so they stay ambiguous; no refresh
  or backfill command rewrites unverified history by design, and the retention window removes
  the rows. Nothing else repairs them.
* The backfill commits in batches, so the rollup writers wait for one batch's row locks at most
  and a run the deploy kills (Railway allows a pre-deploy command 300 seconds) keeps what it
  committed. Each table is a `DO` loop run outside a transaction (the `autocommit_block`), where
  PL/pgSQL may `COMMIT`: `span_rollups_hourly` in ranges of `HOURLY_BATCH` ids between the
  table's min and max id, `user_stats_daily` one project and day at a time through its natural
  key. The worker bypass that takes each loop past row-level security lasts one transaction, so
  every batch sets it again before it reads. What remains: a rerun starts from the first batch
  again and, though it rewrites nothing that is already NULL, reads every row that still holds a
  0 and its spans once more, so it is quicker, not free; a batch takes as long as its hours have
  spans, so the whole run has no time bound; and one project's users of one day are one
  transaction however many there are.
* `attribution_rollups_daily` rows are not rewritten here: their groups (trace name, user,
  release, promoted metadata keys, the folded `other` row) cannot be matched to their spans
  cheaply in SQL. The scheduled refresh rewrites yesterday and today on its next pass; older days
  keep their stored zeros until `spanlight attribution backfill` recomputes them.
* While the old app version still runs during a deploy it writes 0 for those rows again, and its
  readers fail on a NULL total; the next rollup pass over such an hour or day rewrites it.

Downgrade turns every NULL total back into 0, one statement per table as before, and restores
NOT NULL; SET NOT NULL scans each table under its lock.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1043"
down_revision: str | None = "1042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
# Hourly rows (an id range) per backfill transaction.
HOURLY_BATCH = 1000

COLUMNS: dict[str, tuple[str, ...]] = {
    "span_rollups_hourly": ("input_tokens", "output_tokens", "cached_tokens"),
    "attribution_rollups_daily": ("input_tokens", "output_tokens"),
    "user_stats_daily": ("tokens",),
}

# Whether the spans of hourly row `r` report each column (usage rollups left out, as the writer
# does): NULL when none of them is left, so the stored value is kept.
_HOURLY_SPANS = """
    FROM spans AS s
    LEFT JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = r.project_id
      AND s.started_at >= r.bucket_start
      AND s.started_at < r.bucket_start + interval '1 hour'
      AND s.kind = r.kind
      AND s.provider IS NOT DISTINCT FROM r.provider
      AND s.model IS NOT DISTINCT FROM r.model
      AND t.environment IS NOT DISTINCT FROM r.environment"""


def _covered(project: str, start: str, step: str) -> str:
    """`project`'s source history is verified from `start` on and retention or erasure took none
    of it within `step`: the rule of `complete_ranges` in `app.rollups.coverage`, which every
    rollup writer applies. Checked in the update's own snapshot; retention records an hour in the
    transaction that deletes its traces, so a bucket that lost one of two spans keeps its 0."""
    return f"""EXISTS (
              SELECT 1 FROM projects AS p
              WHERE p.id = {project} AND {start} >= p.aggregate_history_unverified_before
          )
          AND NOT EXISTS (
              SELECT 1 FROM aggregate_incomplete_hours AS h
              WHERE h.project_id = {project}
                AND h.bucket_hour >= {start} AND h.bucket_hour < {start} + {step}
          )"""


def _hourly_case(column: str) -> str:
    return f"CASE WHEN r.{column} = 0 AND NOT g.{column} THEN NULL ELSE r.{column} END AS {column}"


def _reports(column: str) -> str:
    return f"bool_or(NOT s.usage_rollup AND s.{column} IS NOT NULL) AS {column}"


_HOURLY_COLUMNS = COLUMNS["span_rollups_hourly"]
_HOURLY_LIST = ", ".join(_HOURLY_COLUMNS)
# One id range of `_HOURLY_BACKFILL`'s loop. Each candidate row's spans are read once; only rows
# whose figures change are written.
_HOURLY_TO_NULL = f"""
    UPDATE span_rollups_hourly AS target
    SET ({_HOURLY_LIST}) = ({", ".join(f"c.{column}" for column in _HOURLY_COLUMNS)})
    FROM (
        SELECT r.id, {", ".join(_hourly_case(column) for column in _HOURLY_COLUMNS)}
        FROM span_rollups_hourly AS r
        CROSS JOIN LATERAL (
            SELECT {", ".join(_reports(column) for column in _HOURLY_COLUMNS)}
            {_HOURLY_SPANS}
        ) AS g
        WHERE r.id >= batch_start AND r.id < batch_start + {HOURLY_BATCH}
          AND ({" OR ".join(f"r.{column} = 0" for column in _HOURLY_COLUMNS)})
          AND {_covered("r.project_id", "r.bucket_start", "interval '1 hour'")}
    ) AS c
    WHERE target.id = c.id
      AND ({", ".join(f"target.{column}" for column in _HOURLY_COLUMNS)})
          IS DISTINCT FROM ({", ".join(f"c.{column}" for column in _HOURLY_COLUMNS)});
"""

# The bypass that lets the loop past row-level security (forced on the owner too) lasts one
# transaction, so every batch sets it again before it reads.
_HOURLY_BACKFILL = f"""
    DO $$
    DECLARE
        batch_start bigint;
        last_id bigint;
    BEGIN
        PERFORM set_config('app.bypass_rls', 'on', true);
        SELECT min(id), max(id) INTO batch_start, last_id FROM span_rollups_hourly;
        WHILE batch_start <= last_id LOOP
            PERFORM set_config('app.bypass_rls', 'on', true);
            {_HOURLY_TO_NULL}
            COMMIT;
            batch_start := batch_start + {HOURLY_BATCH};
        END LOOP;
    END
    $$
"""

# The traces of user-day row `u`, as `app.end_users.refresh` groups them.
_USER_TRACES = """
    FROM traces AS t
    WHERE t.project_id = u.project_id
      AND t.external_user_id = u.external_user_id
      AND t.environment IS NOT DISTINCT FROM u.environment
      AND t.started_at >= u.day::timestamp AT TIME ZONE 'UTC'
      AND t.started_at < (u.day + 1)::timestamp AT TIME ZONE 'UTC'"""

# One project-day of `_USERS_BACKFILL`'s loop, reached through the natural key.
_USERS_TO_NULL = f"""
    UPDATE user_stats_daily AS u
    SET tokens = NULL
    WHERE u.project_id = batch.project_id AND u.day = batch.day
      AND u.tokens = 0
      AND {_covered("u.project_id", "(u.day::timestamp AT TIME ZONE 'UTC')", "interval '1 day'")}
      AND EXISTS (SELECT 1 {_USER_TRACES})
      AND NOT EXISTS (
          SELECT 1 {_USER_TRACES}
            AND EXISTS (
                SELECT 1 FROM spans AS s
                WHERE s.project_id = t.project_id AND s.trace_id = t.trace_id
                  AND s.kind = 'llm' AND NOT s.usage_rollup
                  AND s.started_at >= u.day::timestamp AT TIME ZONE 'UTC'
                  AND (s.input_tokens IS NOT NULL OR s.output_tokens IS NOT NULL)
            )
      );
"""

# PL/pgSQL holds a read-only loop cursor across the first COMMIT, so the project-days are listed
# once, with the bypass of the first transaction.
_USERS_BACKFILL = f"""
    DO $$
    DECLARE
        batch record;
    BEGIN
        PERFORM set_config('app.bypass_rls', 'on', true);
        FOR batch IN SELECT DISTINCT project_id, day FROM user_stats_daily LOOP
            PERFORM set_config('app.bypass_rls', 'on', true);
            {_USERS_TO_NULL}
            COMMIT;
        END LOOP;
    END
    $$
"""


def _alter(table: str, clause: str) -> None:
    op.execute(
        f"ALTER TABLE {table} "
        + ", ".join(f"ALTER COLUMN {column} {clause}" for column in COLUMNS[table])
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        for table in COLUMNS:
            _alter(table, "DROP NOT NULL")
        op.execute("RESET lock_timeout")
        op.execute(_HOURLY_BACKFILL)
        op.execute(_USERS_BACKFILL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for table, columns in COLUMNS.items():
            _with_bypass(
                f"UPDATE {table} SET "
                + ", ".join(f"{column} = coalesce({column}, 0)" for column in columns)
                + " WHERE "
                + " OR ".join(f"{column} IS NULL" for column in columns)
                + ";"
            )
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        for table in COLUMNS:
            _alter(table, "SET NOT NULL")
        op.execute("RESET lock_timeout")


# Copied on purpose from 1016: migrations do not import app code or each other.
def _with_bypass(statement: str) -> None:
    """Run `statement` in one `DO` block with the worker bypass set for its transaction only (the
    caller's value restored afterwards), so row-level security hides no project's rows."""
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
