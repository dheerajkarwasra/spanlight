"""Per-budget hourly spend snapshots, so admission stops re-summing the whole period.

Revision ID: 1032
Revises: 1031
Create Date: 2026-11-01

Block-budget admission (`app.budgets.history.read_history`) summed every priced span of the
UTC period under the counter lock: about 100 ms per million spans, so one busy budget admitted
about ten calls a second. It now sums closed UTC hours from `budget_bucket_snapshots` and scans
only the open hour plus the 12-minute reservation lease
(`docs/specs/2026-11-01-budget-day-snapshots.md`).

* `budget_bucket_snapshots (project_id, budget_id, bucket_start, committed_usd, generation,
  computed_at)`: budget `budget_id`'s committed spend of the hour `[bucket_start, +1h)`, the
  three terms of the history read each keyed by a stable hour: stored spans and ledger rows by
  `started_at`, settled or reaped reservations by the admission clock `expires_at - 12 minutes`.
  The literal is the reservation lease (`app.budgets.reserve.RESERVATION_LEASE`) and part of
  the lease-clock invariant `reclassify_history` already relies on: a lease change must migrate
  this clock first.
* `budget_bucket_versions (project_id, bucket_start, generation)`: bumped (a fresh sequence
  value) by every writer that can change any input of the hour, in the writer's own
  transaction. A snapshot stores the generation it was computed against (0 when no version row
  existed) and is valid while the two match. A generation rather than a "stale" flag because
  the compute statement reads the version and the data in one snapshot: a writer still in
  flight is in neither, and its bump invalidates the row when it commits; a flag cleared by the
  compute could be cleared after such a writer set it and keep a stale row for ever.
* Statement-level triggers do the bumping, so no writer can forget: on `spans` (INSERT, UPDATE,
  DELETE, with transition tables) the hours of the old and new rows, of every current span of
  the touched traces, of their reservations' admission clocks and of their ledger rows; on
  `traces` (UPDATE) the same hours of every trace whose `name`, `external_user_id` or
  `metadata` changed, since a replay of gateway-owned span ids updates the trace but leaves
  its spans alone; on `budget_reservations` (UPDATE, DELETE) the admission hours of the old
  rows. Rows of a project that is gone (cascade) or has no budget are skipped. Hours are
  inserted in `(project_id, bucket_start)` order, before `aggregate_dirty_hours` is touched, so
  the lock order of an ingest transaction is unchanged in kind. A trace delete cascades one
  spans statement per trace, so the deleters bump all their hours in one sorted statement first
  (`app.budgets.buckets.bump_hours_of_traces`) and the cascade finds rows they already hold.
  Because projects without a budget are skipped, creating a project's first budget (and
  enabling one) locks the project row `FOR UPDATE`, which ingest's `KEY SHARE` conflicts with:
  no ingest that skipped the bump is still in flight when the budget becomes visible.
* `budget_reservations_budget_expires_idx` makes the admission-clock range of the reservation
  term an index range; it is built `CONCURRENTLY`, the 0306 pattern.

Both tables carry the 1022 policy pair. Snapshots are a cache: deleting rows is always safe and
is the only repair; downgrade loses nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1032"
down_revision: str | None = "1031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEQUENCE = "budget_bucket_generation_seq"
VERSIONS = "budget_bucket_versions"
SNAPSHOTS = "budget_bucket_snapshots"
INDEX = "budget_reservations_budget_expires_idx"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"


# The spans function: one body per TG_OP, each naming only the transition tables its trigger
# declares (plpgsql plans a statement when it first runs, so an undeclared table in another
# branch is never resolved).
SPANS_SOURCE = {
    "INSERT": "SELECT project_id, trace_id, started_at FROM new_spans",
    "UPDATE": (
        "SELECT project_id, trace_id, started_at FROM old_spans "
        "UNION ALL SELECT project_id, trace_id, started_at FROM new_spans"
    ),
    "DELETE": "SELECT project_id, trace_id, started_at FROM old_spans",
}
# Traces whose scope fields changed: a replay of gateway-owned span ids updates the trace alone.
TRACES_CHANGED = """SELECT n.project_id, n.trace_id FROM new_traces n JOIN old_traces o
                      ON o.project_id = n.project_id AND o.trace_id = n.trace_id
                    WHERE n.name IS DISTINCT FROM o.name
                       OR n.external_user_id IS DISTINCT FROM o.external_user_id
                       OR n.metadata IS DISTINCT FROM o.metadata"""
# Every hour a touched trace's current spans, reservations and ledger rows fall in: the same
# set `app.budgets.buckets.bump_hours_of_traces` bumps before a trace delete.
TRACE_HOURS = """
                     SELECT s.project_id, date_trunc('hour', s.started_at, 'UTC') AS bucket_start
                     FROM spans s JOIN touched t
                       ON t.project_id = s.project_id AND t.trace_id = s.trace_id
                     UNION
                     SELECT r.project_id,
                            date_trunc('hour', r.expires_at - interval '12 minutes', 'UTC')
                     FROM budget_reservations r JOIN touched t
                       ON t.project_id = r.project_id AND t.trace_id = r.trace_id
                     UNION
                     SELECT d.project_id, date_trunc('hour', d.started_at, 'UTC')
                     FROM budget_deleted_spans d JOIN touched t
                       ON t.project_id = d.project_id AND t.trace_id = d.trace_id"""


def _own_hours(source: str) -> str:
    return f"""
                     SELECT project_id, date_trunc('hour', started_at, 'UTC') AS bucket_start
                     FROM ({source}) x
                     UNION"""


def _bump_from(source: str, own_hours: str = "") -> str:
    return f"""
        IF EXISTS (SELECT 1 FROM budgets b
                   WHERE b.project_id IN (SELECT DISTINCT project_id FROM ({source}) x)) THEN
            WITH touched AS (SELECT DISTINCT project_id, trace_id FROM ({source}) x),
                 hours AS ({own_hours}{TRACE_HOURS})
            INSERT INTO {VERSIONS} (project_id, bucket_start, generation)
            SELECT h.project_id, h.bucket_start, nextval('{SEQUENCE}')
            FROM hours h
            WHERE EXISTS (SELECT 1 FROM projects p WHERE p.id = h.project_id)
              AND EXISTS (SELECT 1 FROM budgets b WHERE b.project_id = h.project_id)
            ORDER BY h.project_id, h.bucket_start
            ON CONFLICT (project_id, bucket_start)
            DO UPDATE SET generation = nextval('{SEQUENCE}');
        END IF;
    """


SPANS_FUNCTION = f"""
    CREATE FUNCTION bump_budget_buckets_spans() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            {_bump_from(SPANS_SOURCE["INSERT"], _own_hours(SPANS_SOURCE["INSERT"]))}
        ELSIF TG_OP = 'UPDATE' THEN
            {_bump_from(SPANS_SOURCE["UPDATE"], _own_hours(SPANS_SOURCE["UPDATE"]))}
        ELSE
            {_bump_from(SPANS_SOURCE["DELETE"], _own_hours(SPANS_SOURCE["DELETE"]))}
        END IF;
        RETURN NULL;
    END $$
"""

TRACES_FUNCTION = f"""
    CREATE FUNCTION bump_budget_buckets_traces() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        {_bump_from(TRACES_CHANGED)}
        RETURN NULL;
    END $$
"""

RESERVATIONS_FUNCTION = f"""
    CREATE FUNCTION bump_budget_buckets_reservations() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        INSERT INTO {VERSIONS} (project_id, bucket_start, generation)
        SELECT h.project_id, h.bucket_start, nextval('{SEQUENCE}')
        FROM (SELECT DISTINCT project_id,
                     date_trunc('hour', expires_at - interval '12 minutes', 'UTC') AS bucket_start
              FROM old_reservations) h
        WHERE EXISTS (SELECT 1 FROM projects p WHERE p.id = h.project_id)
          AND EXISTS (SELECT 1 FROM budgets b WHERE b.project_id = h.project_id)
        ORDER BY h.project_id, h.bucket_start
        ON CONFLICT (project_id, bucket_start)
        DO UPDATE SET generation = nextval('{SEQUENCE}');
        RETURN NULL;
    END $$
"""

# (trigger, table, event, transition tables, function)
TRIGGERS = (
    ("spans_budget_buckets_insert", "spans", "INSERT", "NEW TABLE AS new_spans", "spans"),
    (
        "spans_budget_buckets_update",
        "spans",
        "UPDATE",
        "OLD TABLE AS old_spans NEW TABLE AS new_spans",
        "spans",
    ),
    ("spans_budget_buckets_delete", "spans", "DELETE", "OLD TABLE AS old_spans", "spans"),
    (
        "traces_budget_buckets_update",
        "traces",
        "UPDATE",
        "OLD TABLE AS old_traces NEW TABLE AS new_traces",
        "traces",
    ),
    (
        "budget_reservations_budget_buckets_update",
        "budget_reservations",
        "UPDATE",
        "OLD TABLE AS old_reservations",
        "reservations",
    ),
    (
        "budget_reservations_budget_buckets_delete",
        "budget_reservations",
        "DELETE",
        "OLD TABLE AS old_reservations",
        "reservations",
    ),
)


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


def _index_is_valid(name: str) -> bool | None:
    """True if the index exists and is valid, False if INVALID, None if absent (or offline)."""
    context = op.get_context()
    if context.as_sql:
        return None
    row = context.bind.execute(  # type: ignore[union-attr]  # bind is set when not as_sql
        sa.text(
            "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND c.relnamespace = current_schema()::regnamespace"
        ),
        {"name": name},
    ).first()
    return None if row is None else bool(row[0])


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(f"CREATE SEQUENCE {SEQUENCE} AS bigint NO CYCLE")
    op.execute(
        f"""
        CREATE TABLE {VERSIONS} (
            project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            bucket_start timestamptz NOT NULL
                         CHECK (bucket_start = date_trunc('hour', bucket_start, 'UTC')),
            generation   bigint NOT NULL CHECK (generation > 0),
            PRIMARY KEY (project_id, bucket_start)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {SNAPSHOTS} (
            project_id    uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            budget_id     uuid NOT NULL REFERENCES budgets (id) ON DELETE CASCADE,
            bucket_start  timestamptz NOT NULL
                          CHECK (bucket_start = date_trunc('hour', bucket_start, 'UTC')),
            committed_usd numeric(18, 8) NOT NULL,
            generation    bigint NOT NULL CHECK (generation >= 0),
            computed_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, budget_id, bucket_start)
        )
        """
    )
    _enable_rls(VERSIONS)
    _enable_rls(SNAPSHOTS)
    op.execute(SPANS_FUNCTION)
    op.execute(TRACES_FUNCTION)
    op.execute(RESERVATIONS_FUNCTION)
    for name, table, event, referencing, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} AFTER {event} ON {table} REFERENCING {referencing} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION bump_budget_buckets_{function}()"
        )
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        state = _index_is_valid(INDEX)
        if state is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        if state is not True:
            op.execute(
                f"CREATE INDEX CONCURRENTLY {INDEX} "
                "ON budget_reservations (budget_id, expires_at) "
                "WHERE status IN ('settled', 'reaped')"
            )
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    for name, table, _, _, _ in TRIGGERS:
        op.execute(f"DROP TRIGGER {name} ON {table}")
    for function in ("spans", "traces", "reservations"):
        op.execute(f"DROP FUNCTION bump_budget_buckets_{function}()")
    op.execute(f"DROP TABLE {SNAPSHOTS}")
    op.execute(f"DROP TABLE {VERSIONS}")
    op.execute(f"DROP SEQUENCE {SEQUENCE}")
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute("RESET lock_timeout")
