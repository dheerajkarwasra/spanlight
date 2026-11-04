"""Reserve fresh budget history in one client round trip.

Revision ID: 1042
Revises: 1041

Run ensure-app-role after upgrading to grant the configured app role execution.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1042"
down_revision: str | None = "1041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIGNATURE = (
    "public.budget_reserve_fast(uuid, uuid[], timestamptz[], timestamptz[], numeric[], "
    "boolean[], numeric, text, text, uuid[], timestamptz, timestamptz)"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("""
CREATE FUNCTION public.budget_reserve_fast(
    p_project_id uuid,
    p_budget_ids uuid[],
    p_periods timestamptz[],
    p_lasts timestamptz[],
    p_amounts numeric[],
    p_monthly boolean[],
    p_estimate numeric,
    p_trace_id text,
    p_span_id text,
    p_reservation_ids uuid[],
    p_expires_at timestamptz,
    p_now timestamptz
) RETURNS TABLE (
    status text, budget_id uuid, committed_usd numeric,
    history_unknown boolean, legacy_uncertain boolean
)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $function$
-- The copied history SQL uses column names also present in the return signature.
#variable_conflict use_column
DECLARE
    budget_count integer := cardinality(p_budget_ids);
    n bigint;
    v_budget record;
    v_history record;
    v_committed numeric;
    v_unknown boolean;
BEGIN
    IF (p_project_id = current_setting('app.project_id')::uuid) IS NOT TRUE THEN
        RAISE EXCEPTION 'Budget reservation requires the matching project binding';
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Budget reservation requires READ COMMITTED';
    END IF;
    IF budget_count IS NULL
       OR cardinality(p_periods) IS DISTINCT FROM budget_count
       OR cardinality(p_lasts) IS DISTINCT FROM budget_count
       OR cardinality(p_amounts) IS DISTINCT FROM budget_count
       OR cardinality(p_monthly) IS DISTINCT FROM budget_count
       OR cardinality(p_reservation_ids) IS DISTINCT FROM budget_count THEN
        RAISE EXCEPTION 'Budget reservation arrays must have equal lengths';
    END IF;

    -- Like reserve._lock_counters(all_or_none=True), lock nothing on a period rollover
    -- until every counter exists; partial locks could deadlock concurrent first admissions.
    PERFORM c.budget_id FROM budget_counters c
    WHERE c.project_id = p_project_id
      AND (c.budget_id, c.period_start) IN (
          SELECT * FROM unnest(p_budget_ids, p_periods))
      AND (SELECT count(*) FROM budget_counters existing
           WHERE existing.project_id = p_project_id
             AND (existing.budget_id, existing.period_start) IN (
                 SELECT * FROM unnest(p_budget_ids, p_periods))) = budget_count
    ORDER BY c.budget_id FOR UPDATE;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n < budget_count THEN
        RETURN QUERY SELECT 'slow'::text, NULL::uuid, NULL::numeric, NULL::boolean, NULL::boolean;
        RETURN;
    END IF;

    FOR v_budget IN
        SELECT b.* FROM unnest(p_budget_ids, p_periods, p_lasts, p_amounts, p_monthly)
            WITH ORDINALITY AS b(id, started, last, amount, monthly, position)
        ORDER BY b.position
    LOOP
        -- Source: app.budgets.history._HOURS, copied verbatim apart from parameters/INTO.
        -- Keep this SQL and history._HOURS in sync; each read must start after the counter lock.
        WITH hours AS (
            SELECT h.hour, s.committed_usd,
                   s.generation IS DISTINCT FROM coalesce(v.generation, 0) AS stale,
                   EXISTS (SELECT 1 FROM budget_bucket_deltas d
                           WHERE d.project_id = p_project_id AND d.budget_id = v_budget.id
                             AND d.bucket_start = h.hour) AS pending
            FROM generate_series(CAST(v_budget.started AS timestamptz),
                                 CAST(v_budget.last AS timestamptz),
                                 interval '1 hour') AS h(hour)
            LEFT JOIN budget_bucket_snapshots s ON s.project_id = p_project_id
                                               AND s.budget_id = v_budget.id
                                               AND s.bucket_start = h.hour
            LEFT JOIN budget_bucket_versions v ON v.project_id = p_project_id
                                              AND v.bucket_start = h.hour),
        locked AS (
            SELECT s.bucket_start, s.committed_usd FROM budget_bucket_snapshots s
            WHERE s.project_id = p_project_id AND s.budget_id = v_budget.id
              AND s.bucket_start IN (SELECT hour FROM hours WHERE pending AND NOT stale)
            ORDER BY s.bucket_start FOR UPDATE),
        gone AS (
            DELETE FROM budget_bucket_deltas d
            WHERE d.project_id = p_project_id AND d.budget_id = v_budget.id
              AND d.bucket_start IN (SELECT bucket_start FROM locked)
            RETURNING d.bucket_start, d.amount_usd),
        folded AS (
            SELECT bucket_start, sum(amount_usd) AS total FROM gone GROUP BY bucket_start),
        stored AS (
            UPDATE budget_bucket_snapshots s
            SET committed_usd = l.committed_usd + coalesce(f.total, 0), computed_at = now()
            FROM locked l LEFT JOIN folded f ON f.bucket_start = l.bucket_start
            WHERE s.project_id = p_project_id AND s.budget_id = v_budget.id
              AND s.bucket_start = l.bucket_start
            RETURNING s.committed_usd)
        SELECT p.budget_history_missing_through, p.retention_days,
               c.legacy_unattributed_usd, c.reserved_usd,
               (SELECT coalesce(sum(committed_usd) FILTER (WHERE NOT stale AND NOT pending), 0)
                FROM hours) AS valid_usd,
               (SELECT coalesce(sum(committed_usd), 0) FROM stored) AS folded_usd,
               (SELECT array_agg(hour ORDER BY hour) FILTER (WHERE stale) FROM hours) AS touched,
               (SELECT array_agg(hour ORDER BY hour) FILTER (WHERE stale) FROM hours) AS stale,
               (SELECT array_agg(hour ORDER BY hour) FILTER (WHERE committed_usd IS NULL)
                FROM hours) AS missing
        INTO STRICT v_history
        FROM projects p JOIN budget_counters c ON c.project_id = p.id
        WHERE p.id = p_project_id AND c.budget_id = v_budget.id
          AND c.period_start = v_budget.started;
        IF cardinality(v_history.touched) > 0 OR cardinality(v_history.missing) > 0 THEN
            RETURN QUERY SELECT 'slow'::text, NULL::uuid, NULL::numeric,
                                NULL::boolean, NULL::boolean;
            RETURN;
        END IF;
        v_committed := v_history.valid_usd + v_history.folded_usd
                       + v_history.legacy_unattributed_usd + v_history.reserved_usd;
        v_unknown := (v_history.budget_history_missing_through IS NOT NULL
                      AND v_history.budget_history_missing_through >= v_budget.started)
                     OR v_history.retention_days < CASE WHEN v_budget.monthly THEN 31 ELSE 1 END;
        IF v_unknown OR NOT (v_committed + p_estimate <= v_budget.amount) IS TRUE THEN
            RETURN QUERY SELECT 'refused'::text, v_budget.id, v_committed, v_unknown,
                                v_history.legacy_unattributed_usd > 0;
            RETURN;
        END IF;
    END LOOP;

    -- Source: app.budgets.reserve._HOLD (counter hold and reservation insert).
    -- Keep both statements in sync; all counter rows are already locked above.
    WITH reserved AS (
        UPDATE budget_counters SET reserved_usd = reserved_usd + p_estimate, updated_at = p_now
        WHERE (budget_id, period_start) IN (
            SELECT * FROM unnest(CAST(p_budget_ids AS uuid[]), CAST(p_periods AS timestamptz[]))))
    INSERT INTO budget_reservations
           (id, project_id, budget_id, period_start, trace_id, span_id, amount_usd, status,
            expires_at)
    SELECT r.id, p_project_id, r.budget_id, r.period_start, p_trace_id, p_span_id, p_estimate,
           'held', p_expires_at
    FROM unnest(CAST(p_reservation_ids AS uuid[]), CAST(p_budget_ids AS uuid[]),
                CAST(p_periods AS timestamptz[])) AS r(id, budget_id, period_start);
    RETURN QUERY SELECT 'reserved'::text, NULL::uuid, NULL::numeric, NULL::boolean, NULL::boolean;
END
$function$;
    """)
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(f"DROP FUNCTION {_SIGNATURE}")
