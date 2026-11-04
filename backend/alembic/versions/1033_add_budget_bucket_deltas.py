"""Every hour of a budget's period is a snapshot row, kept exact by writer deltas.

Revision ID: 1033
Revises: 1032
Create Date: 2026-11-01

Migration 1032 snapshotted closed hours and scanned the open one on every admission, which
re-read every settled call of the open hour under the counter lock (spec
`docs/specs/2026-11-01-budget-day-snapshots.md` §8). Now the open hour is a row like any
other, and a writer that can price its change exactly appends one row to
`budget_bucket_deltas (project_id, budget_id, bucket_start, seq, amount_usd)`: an INSERT takes
no row lock and never waits, so writers keep their lock order and cannot cycle with admission.
The hour's true total is `committed_usd + sum(deltas)`; admission (`app.budgets.compaction`)
and the reaper's job fold the deltas into the row under its lock. A writer that cannot price
its change bumps the hour's version as before, and the next admission recomputes the hour from
the data, deleting its deltas in the same statement (one snapshot: the deltas it deletes are
exactly those whose data it sees).

* `bump_budget_buckets_spans()` replaced: a span write of an identity that no settled or reaped
  reservation and no ledger row tracks (a held reservation contributes nothing to any term, so
  the gateway's own span writes are priced exactly) appends, per budget of the project, the
  change of its value: `value_b(new) - value_b(old)`, old to the old hour and new to the new,
  where `value_b` is the span's cost when it is not a usage rollup, matches the budget's scope
  (the same rules as `app.budgets.compaction`'s scope SQL) and holds no reservation of that
  budget; a zero change writes no row. Tracked identities bump their span hours, their traces'
  admission hours and ledger hours; a DELETE always bumps. The "every current span of the
  touched traces" lookup is gone: the `traces` trigger owns trace-field changes.
* `bump_budget_buckets_reservations()` replaced: a `held -> settled` transition of a row that
  is not reclassified, in its own period, bumps nothing (`reserve.settle` appends the charge
  at the admission hour); every other change bumps. The UPDATE trigger now also declares the
  new rows.
* `budget_bucket_snapshots.generation >= -1`: a compactor creates a missing row at `-1`,
  which is never a version, so the row stays stale until an admission computes it.

Downgrade restores the 1032 function bodies and trigger verbatim, deletes the `-1` rows
(1032 recomputes a missing hour), restores the CHECK and drops the table and the sequence.
Rows compacted from deltas stay exact: their generation still matches the version.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1033"
down_revision: str | None = "1032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEQUENCE = "budget_bucket_delta_seq"
VERSIONS = "budget_bucket_versions"
SNAPSHOTS = "budget_bucket_snapshots"
DELTAS = "budget_bucket_deltas"
GENERATION_SEQUENCE = "budget_bucket_generation_seq"
UPDATE_TRIGGER = "budget_reservations_budget_buckets_update"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"
# `app.core.model_match.SNAPSHOT_SUFFIX_REGEX`: a dated or `-latest` snapshot of the model.
SNAPSHOT_SUFFIX_REGEX = "^-([0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}|latest)$"

# The budget's scope on a span `x` and its trace `t`: `app.budgets.compaction._SCOPE` in SQL.
SCOPE = f"""CASE b.scope
                 WHEN 'project' THEN TRUE
                 WHEN 'gateway_key' THEN x.source_key_id = CAST(b.scope_id AS uuid)
                 WHEN 'model' THEN x.model = b.scope_id
                      OR (left(x.model, length(b.scope_id)) = b.scope_id
                          AND substr(x.model, length(b.scope_id) + 1) ~ '{SNAPSHOT_SUFFIX_REGEX}')
                 WHEN 'user' THEN t.external_user_id = b.scope_id
                 WHEN 'trace_name' THEN t.name = b.scope_id
                 WHEN 'metadata' THEN t.metadata @> jsonb_build_object(
                      split_part(b.scope_id, '=', 1),
                      substr(b.scope_id, position('=' in b.scope_id) + 1))
             END"""

COLUMNS = "project_id, trace_id, span_id, started_at, cost_usd, usage_rollup, model, source_key_id"
# The rows of each statement with the sign of their contribution: old rows leave, new rows arrive.
SPANS_SOURCE = {
    "INSERT": f"SELECT {COLUMNS}, 1 AS sign FROM new_spans",
    "UPDATE": (
        f"SELECT {COLUMNS}, -1 AS sign FROM old_spans "
        f"UNION ALL SELECT {COLUMNS}, 1 AS sign FROM new_spans"
    ),
    "DELETE": f"SELECT {COLUMNS}, -1 AS sign FROM old_spans",
}


def _bump(hours: str) -> str:
    return f"""
            INSERT INTO {VERSIONS} (project_id, bucket_start, generation)
            SELECT h.project_id, h.bucket_start, nextval('{GENERATION_SEQUENCE}')
            FROM ({hours}) h
            WHERE EXISTS (SELECT 1 FROM projects p WHERE p.id = h.project_id)
              AND EXISTS (SELECT 1 FROM budgets b WHERE b.project_id = h.project_id)
            ORDER BY h.project_id, h.bucket_start
            ON CONFLICT (project_id, bucket_start)
            DO UPDATE SET generation = nextval('{GENERATION_SEQUENCE}')"""


# Hours of the tracked rows: their own, their traces' admission clocks and ledger rows.
TRACKED_HOURS = """
                     SELECT x.project_id, date_trunc('hour', x.started_at, 'UTC') AS bucket_start
                     FROM rows x JOIN tracked k
                       ON k.project_id = x.project_id AND k.trace_id = x.trace_id
                      AND k.span_id = x.span_id
                     UNION
                     SELECT r.project_id,
                            date_trunc('hour', r.expires_at - interval '12 minutes', 'UTC')
                     FROM budget_reservations r
                     JOIN (SELECT DISTINCT project_id, trace_id FROM tracked) k
                       ON k.project_id = r.project_id AND k.trace_id = r.trace_id
                     UNION
                     SELECT d.project_id, date_trunc('hour', d.started_at, 'UTC')
                     FROM budget_deleted_spans d
                     JOIN (SELECT DISTINCT project_id, trace_id FROM tracked) k
                       ON k.project_id = d.project_id AND k.trace_id = d.trace_id"""


def _write_body(source: str) -> str:
    """INSERT or UPDATE: deltas for the untracked identities, bumps for the tracked ones."""
    return f"""
        IF EXISTS (SELECT 1 FROM budgets b
                   WHERE b.project_id IN (SELECT DISTINCT project_id FROM ({source}) x)) THEN
            WITH rows AS ({source}),
                 tracked AS (
                     SELECT DISTINCT x.project_id, x.trace_id, x.span_id FROM rows x
                     WHERE EXISTS (SELECT 1 FROM budget_reservations r
                                   WHERE r.project_id = x.project_id AND r.trace_id = x.trace_id
                                     AND r.span_id = x.span_id
                                     AND r.status IN ('settled', 'reaped'))
                        OR EXISTS (SELECT 1 FROM budget_deleted_spans d
                                   WHERE d.project_id = x.project_id AND d.trace_id = x.trace_id
                                     AND d.span_id = x.span_id)),
                 deltas AS (
                     INSERT INTO {DELTAS} (project_id, budget_id, bucket_start, amount_usd)
                     SELECT x.project_id, b.id, date_trunc('hour', x.started_at, 'UTC'),
                            sum(x.sign * x.cost_usd)
                     FROM rows x
                     JOIN budgets b ON b.project_id = x.project_id
                     LEFT JOIN traces t ON t.project_id = x.project_id AND t.trace_id = x.trace_id
                     WHERE x.cost_usd IS NOT NULL AND NOT x.usage_rollup
                       AND NOT EXISTS (SELECT 1 FROM tracked k
                                       WHERE k.project_id = x.project_id
                                         AND k.trace_id = x.trace_id AND k.span_id = x.span_id)
                       AND NOT EXISTS (SELECT 1 FROM budget_reservations r
                                       WHERE r.project_id = x.project_id AND r.budget_id = b.id
                                         AND r.trace_id = x.trace_id AND r.span_id = x.span_id)
                       AND {SCOPE}
                     GROUP BY x.project_id, b.id, date_trunc('hour', x.started_at, 'UTC')
                     HAVING sum(x.sign * x.cost_usd) <> 0
                     RETURNING 1)
            {_bump(TRACKED_HOURS)};
        END IF;
    """


def _delete_body(source: str) -> str:
    """DELETE: bump the rows' hours, their traces' admission hours and ledger rows."""
    return f"""
        IF EXISTS (SELECT 1 FROM budgets b
                   WHERE b.project_id IN (SELECT DISTINCT project_id FROM ({source}) x)) THEN
            WITH rows AS ({source}),
                 tracked AS (SELECT DISTINCT project_id, trace_id, span_id FROM rows)
            {_bump(TRACKED_HOURS)};
        END IF;
    """


SPANS_FUNCTION = f"""
    CREATE OR REPLACE FUNCTION bump_budget_buckets_spans() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            {_write_body(SPANS_SOURCE["INSERT"])}
        ELSIF TG_OP = 'UPDATE' THEN
            {_write_body(SPANS_SOURCE["UPDATE"])}
        ELSE
            {_delete_body(SPANS_SOURCE["DELETE"])}
        END IF;
        RETURN NULL;
    END $$
"""

RESERVATION_HOURS = """SELECT DISTINCT o.project_id,
                            date_trunc('hour', o.expires_at - interval '12 minutes', 'UTC')
                                AS bucket_start
                     FROM old_reservations o"""

SETTLED_IN_PLACE = """
                     JOIN new_reservations n ON n.id = o.id
                     WHERE NOT (o.status = 'held' AND n.status = 'settled'
                                AND NOT n.reclassified AND o.period_start = n.period_start)"""

RESERVATIONS_FUNCTION = f"""
    CREATE OR REPLACE FUNCTION bump_budget_buckets_reservations() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            {_bump(RESERVATION_HOURS + SETTLED_IN_PLACE)};
        ELSE
            {_bump(RESERVATION_HOURS)};
        END IF;
        RETURN NULL;
    END $$
"""

# Migration 1032's bodies, verbatim, for the downgrade.
SPANS_SOURCE_1032 = {
    "INSERT": "SELECT project_id, trace_id, started_at FROM new_spans",
    "UPDATE": (
        "SELECT project_id, trace_id, started_at FROM old_spans "
        "UNION ALL SELECT project_id, trace_id, started_at FROM new_spans"
    ),
    "DELETE": "SELECT project_id, trace_id, started_at FROM old_spans",
}
TRACE_HOURS_1032 = """
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


def _own_hours_1032(source: str) -> str:
    return f"""
                     SELECT project_id, date_trunc('hour', started_at, 'UTC') AS bucket_start
                     FROM ({source}) x
                     UNION"""


def _bump_from_1032(source: str, own_hours: str = "") -> str:
    return f"""
        IF EXISTS (SELECT 1 FROM budgets b
                   WHERE b.project_id IN (SELECT DISTINCT project_id FROM ({source}) x)) THEN
            WITH touched AS (SELECT DISTINCT project_id, trace_id FROM ({source}) x),
                 hours AS ({own_hours}{TRACE_HOURS_1032})
            INSERT INTO {VERSIONS} (project_id, bucket_start, generation)
            SELECT h.project_id, h.bucket_start, nextval('{GENERATION_SEQUENCE}')
            FROM hours h
            WHERE EXISTS (SELECT 1 FROM projects p WHERE p.id = h.project_id)
              AND EXISTS (SELECT 1 FROM budgets b WHERE b.project_id = h.project_id)
            ORDER BY h.project_id, h.bucket_start
            ON CONFLICT (project_id, bucket_start)
            DO UPDATE SET generation = nextval('{GENERATION_SEQUENCE}');
        END IF;
    """


_BODIES_1032 = {
    name: _bump_from_1032(source, _own_hours_1032(source))
    for name, source in SPANS_SOURCE_1032.items()
}
SPANS_FUNCTION_1032 = f"""
    CREATE OR REPLACE FUNCTION bump_budget_buckets_spans() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            {_BODIES_1032["INSERT"]}
        ELSIF TG_OP = 'UPDATE' THEN
            {_BODIES_1032["UPDATE"]}
        ELSE
            {_BODIES_1032["DELETE"]}
        END IF;
        RETURN NULL;
    END $$
"""

RESERVATIONS_FUNCTION_1032 = f"""
    CREATE OR REPLACE FUNCTION bump_budget_buckets_reservations() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        INSERT INTO {VERSIONS} (project_id, bucket_start, generation)
        SELECT h.project_id, h.bucket_start, nextval('{GENERATION_SEQUENCE}')
        FROM (SELECT DISTINCT project_id,
                     date_trunc('hour', expires_at - interval '12 minutes', 'UTC') AS bucket_start
              FROM old_reservations) h
        WHERE EXISTS (SELECT 1 FROM projects p WHERE p.id = h.project_id)
          AND EXISTS (SELECT 1 FROM budgets b WHERE b.project_id = h.project_id)
        ORDER BY h.project_id, h.bucket_start
        ON CONFLICT (project_id, bucket_start)
        DO UPDATE SET generation = nextval('{GENERATION_SEQUENCE}');
        RETURN NULL;
    END $$
"""


def _update_trigger(referencing: str) -> str:
    return (
        f"CREATE TRIGGER {UPDATE_TRIGGER} AFTER UPDATE ON budget_reservations "
        f"REFERENCING {referencing} FOR EACH STATEMENT "
        "EXECUTE FUNCTION bump_budget_buckets_reservations()"
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


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(f"CREATE SEQUENCE {SEQUENCE} AS bigint NO CYCLE")
    op.execute(
        f"""
        CREATE TABLE {DELTAS} (
            project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            budget_id    uuid NOT NULL REFERENCES budgets (id) ON DELETE CASCADE,
            bucket_start timestamptz NOT NULL
                         CHECK (bucket_start = date_trunc('hour', bucket_start, 'UTC')),
            seq          bigint NOT NULL DEFAULT nextval('{SEQUENCE}'),
            amount_usd   numeric(18, 8) NOT NULL,
            PRIMARY KEY (project_id, budget_id, bucket_start, seq)
        )
        """
    )
    _enable_rls(DELTAS)
    op.execute(f"ALTER TABLE {SNAPSHOTS} DROP CONSTRAINT {SNAPSHOTS}_generation_check")
    op.execute(
        f"ALTER TABLE {SNAPSHOTS} ADD CONSTRAINT {SNAPSHOTS}_generation_check "
        "CHECK (generation >= -1)"
    )
    op.execute(SPANS_FUNCTION)
    op.execute(RESERVATIONS_FUNCTION)
    op.execute(f"DROP TRIGGER {UPDATE_TRIGGER} ON budget_reservations")
    op.execute(_update_trigger("OLD TABLE AS old_reservations NEW TABLE AS new_reservations"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(f"DROP TRIGGER {UPDATE_TRIGGER} ON budget_reservations")
    op.execute(_update_trigger("OLD TABLE AS old_reservations"))
    op.execute(SPANS_FUNCTION_1032)
    op.execute(RESERVATIONS_FUNCTION_1032)
    op.execute(f"DELETE FROM {SNAPSHOTS} WHERE generation = -1")
    op.execute(f"ALTER TABLE {SNAPSHOTS} DROP CONSTRAINT {SNAPSHOTS}_generation_check")
    op.execute(
        f"ALTER TABLE {SNAPSHOTS} ADD CONSTRAINT {SNAPSHOTS}_generation_check "
        "CHECK (generation >= 0)"
    )
    op.execute(f"DROP TABLE {DELTAS}")
    op.execute(f"DROP SEQUENCE {SEQUENCE}")
