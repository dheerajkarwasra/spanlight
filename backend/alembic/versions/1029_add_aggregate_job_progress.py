"""Admit durable daily and recent aggregate scheduling obligations.

Revision ID: 1029
Revises: 1028

Drain old workers before upgrade; resume only with the new daily AND recent read
and recovery paths installed. API project creation waits for the admission lock.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1029"
down_revision: str | None = "1028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(r"""
LOCK TABLE public.projects IN SHARE ROW EXCLUSIVE MODE;

CREATE SEQUENCE public.aggregate_job_attempt_seq
    AS bigint MINVALUE 1 START WITH 1 INCREMENT BY 1 NO CYCLE CACHE 1;

CREATE TABLE public.aggregate_job_progress (
    project_id uuid NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    kind text NOT NULL,
    attempt_order bigint NOT NULL DEFAULT 0,
    last_attempted_at timestamptz,
    first_period date,
    settled_period date,
    active_period date,
    next_day date,
    active_outcomes smallint NOT NULL DEFAULT 0,
    settled_outcomes smallint NOT NULL DEFAULT 0,
    expired_through_period date,
    dispatch_number bigint NOT NULL DEFAULT 0,
    queued_key text,
    admitted_hour timestamptz,
    next_closed_hour timestamptz,
    closed_pending_since timestamptz,
    CONSTRAINT aggregate_job_progress_pkey PRIMARY KEY (project_id, kind),
    CONSTRAINT aggregate_job_progress_kind CHECK
        (kind IN ('attribution_daily', 'rollup_recent')),
    CONSTRAINT aggregate_job_progress_attempt CHECK (attempt_order >= 0),
    CONSTRAINT aggregate_job_progress_dispatch CHECK (dispatch_number >= 0),
    CONSTRAINT aggregate_job_progress_outcomes CHECK
        (active_outcomes BETWEEN 0 AND 7 AND settled_outcomes BETWEEN 0 AND 7),
    CONSTRAINT aggregate_job_progress_finite_attempt CHECK
        (last_attempted_at IS NULL OR isfinite(last_attempted_at)),
    -- IS TRUE prevents a nullable operand from passing CHECK as UNKNOWN.
    CONSTRAINT aggregate_job_progress_shape CHECK ((CASE kind
        WHEN 'attribution_daily' THEN
            first_period IS NOT NULL AND isfinite(first_period)
            AND settled_period IS NOT NULL AND isfinite(settled_period)
            AND settled_period >= first_period - 1
            AND admitted_hour IS NULL AND next_closed_hour IS NULL
            AND closed_pending_since IS NULL
            AND (expired_through_period IS NULL OR
                (isfinite(expired_through_period)
                 AND expired_through_period BETWEEN first_period AND settled_period))
            AND ((settled_period = first_period - 1 AND settled_outcomes = 0
                  AND expired_through_period IS NULL)
                 OR (settled_period >= first_period AND settled_outcomes BETWEEN 1 AND 7))
            AND ((active_period IS NULL AND next_day IS NULL
                  AND active_outcomes = 0 AND queued_key IS NULL)
                 OR (active_period IS NOT NULL AND isfinite(active_period)
                     AND active_period = settled_period + 1
                     AND next_day IS NOT NULL AND isfinite(next_day)
                     AND next_day BETWEEN active_period - 7 AND active_period - 2))
            AND (queued_key IS NULL OR
                 (length(queued_key) > 0 AND dispatch_number > 0))
        WHEN 'rollup_recent' THEN
            first_period IS NULL AND settled_period IS NULL
            AND active_period IS NULL AND next_day IS NULL
            AND active_outcomes = 0 AND settled_outcomes = 0
            AND expired_through_period IS NULL
            AND dispatch_number = 0 AND queued_key IS NULL
            AND admitted_hour IS NOT NULL AND isfinite(admitted_hour)
            AND next_closed_hour IS NOT NULL AND isfinite(next_closed_hour)
            AND admitted_hour = date_trunc('hour', admitted_hour, 'UTC')
            AND next_closed_hour = date_trunc('hour', next_closed_hour, 'UTC')
            AND next_closed_hour >= admitted_hour
            AND (closed_pending_since IS NULL OR isfinite(closed_pending_since))
        ELSE false
    END) IS TRUE)
);

CREATE INDEX aggregate_job_progress_order
    ON public.aggregate_job_progress (kind, attempt_order, project_id);
-- The PK's project_id prefix is also the child-FK/cascade/read lookup index.

CREATE FUNCTION public.aggregate_job_progress_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.first_period IS DISTINCT FROM OLD.first_period
       OR NEW.admitted_hour IS DISTINCT FROM OLD.admitted_hour THEN
        RAISE EXCEPTION 'Aggregate scheduling admission is immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER aggregate_job_progress_immutable
    BEFORE UPDATE ON public.aggregate_job_progress
    FOR EACH ROW EXECUTE FUNCTION public.aggregate_job_progress_immutable();

CREATE FUNCTION public.admit_aggregate_job_progress() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
-- Restore the caller explicitly on success; transaction/savepoint rollback
-- restores bindings on errors. PG17 represents an initially absent custom GUC
-- as empty afterward; NULLIF treats both as no project under forced RLS.
AS $function$
DECLARE
    previous_project text := current_setting('app.project_id', true);
    admitted_at timestamptz := clock_timestamp();
    period date := (admitted_at AT TIME ZONE 'UTC')::date;
    first_hour timestamptz := date_trunc('hour', admitted_at, 'UTC') - interval '2 hours';
BEGIN
    PERFORM pg_catalog.set_config('app.project_id', NEW.id::text, true);
    INSERT INTO public.aggregate_job_progress
        (project_id, kind, first_period, settled_period)
    VALUES (NEW.id, 'attribution_daily', period, period - 1);
    INSERT INTO public.aggregate_job_progress
        (project_id, kind, admitted_hour, next_closed_hour)
    VALUES (NEW.id, 'rollup_recent', first_hour, first_hour);
    PERFORM pg_catalog.set_config('app.project_id', coalesce(previous_project, ''), true);
    RETURN NEW;
END
$function$;

-- Do not use ON CONFLICT DO NOTHING on progress rows: an unexpected conflict is
-- corruption or an unsupported writer, and must roll back project creation.
-- INSERT projects ON CONFLICT DO NOTHING fires no AFTER INSERT for skipped rows;
-- a committed existing project already has both rows from seed/earlier insertion.
CREATE TRIGGER projects_admit_aggregate_job_progress
    AFTER INSERT ON public.projects
    FOR EACH ROW EXECUTE FUNCTION public.admit_aggregate_job_progress();

-- Seed before enabling RLS, while every new object is still uncommitted/private.
-- No API/trigger bypass, owner privilege escalation or source evidence writes.
-- The project lock makes seed and future trigger coverage one atomic cutover.
DO $seed$
DECLARE
    admitted_at timestamptz := clock_timestamp();
    period date := (admitted_at AT TIME ZONE 'UTC')::date;
    first_hour timestamptz := date_trunc('hour', admitted_at, 'UTC') - interval '2 hours';
BEGIN
    INSERT INTO public.aggregate_job_progress
        (project_id, kind, first_period, settled_period)
    SELECT id, 'attribution_daily', period, period - 1 FROM public.projects;
    INSERT INTO public.aggregate_job_progress
        (project_id, kind, admitted_hour, next_closed_hour)
    SELECT id, 'rollup_recent', first_hour, first_hour FROM public.projects;
END
$seed$;

ALTER TABLE public.aggregate_job_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.aggregate_job_progress FORCE ROW LEVEL SECURITY;
CREATE POLICY aggregate_job_progress_project_isolation
    ON public.aggregate_job_progress
    USING (project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid))
    WITH CHECK (project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid));
CREATE POLICY aggregate_job_progress_worker_bypass
    ON public.aggregate_job_progress
    USING ((SELECT current_setting('app.bypass_rls', true)) = 'on')
    WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'on');

REVOKE ALL ON FUNCTION public.admit_aggregate_job_progress() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.aggregate_job_progress_immutable() FROM PUBLIC;
-- Before reopening creation, ensure-app-role must grant configured ordinary
-- roles data/sequence privileges and these signatures via APP_FUNCTIONS.
-- Pre-upgrade defaults cover table/sequence grants, not this explicit function
-- inventory. Keep creation paused until post-upgrade grants have been checked.
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(r"""
LOCK TABLE public.projects IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.aggregate_job_progress IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.jobs IN SHARE MODE;

DO $guard$
DECLARE
    checked_at timestamptz := clock_timestamp();
    horizon date := (checked_at AT TIME ZONE 'UTC')::date;
    closed_target timestamptz := date_trunc('hour', checked_at - interval '10 minutes', 'UTC');
    previous_bypass text := current_setting('app.bypass_rls', true);
BEGIN
    -- Migration/maintenance inspection only. The admission trigger never sets
    -- this flag. Match existing forced-RLS migration guard conventions.
    PERFORM pg_catalog.set_config('app.bypass_rls', 'on', true);
    IF EXISTS (
        SELECT 1 FROM public.projects p
        CROSS JOIN (VALUES ('attribution_daily'::text), ('rollup_recent'::text)) k(kind)
        WHERE NOT EXISTS (
            SELECT 1 FROM public.aggregate_job_progress g
            WHERE g.project_id = p.id AND g.kind = k.kind
        )
    ) THEN
        RAISE EXCEPTION 'Aggregate scheduling admission is missing; retain progress schema';
    END IF;

    -- Catalog CHECKs protect ordinary inconsistent states; explicitly fail
    -- closed on missing/nonfinite/out-of-order required epoch fields as well.
    IF EXISTS (
        SELECT 1 FROM public.aggregate_job_progress g
        WHERE g.kind NOT IN ('attribution_daily', 'rollup_recent')
           OR (g.kind = 'attribution_daily' AND (
               g.first_period IS NULL OR NOT isfinite(g.first_period)
               OR g.settled_period IS NULL OR NOT isfinite(g.settled_period)
               OR g.settled_period < g.first_period - 1
               OR g.active_period IS NOT NULL OR g.next_day IS NOT NULL
               OR g.active_outcomes <> 0
               OR g.settled_period < greatest(horizon, g.first_period - 1)))
           OR (g.kind = 'rollup_recent' AND (
               g.admitted_hour IS NULL OR NOT isfinite(g.admitted_hour)
               OR g.next_closed_hour IS NULL OR NOT isfinite(g.next_closed_hour)
               OR g.admitted_hour <> date_trunc('hour', g.admitted_hour, 'UTC')
               OR g.next_closed_hour <> date_trunc('hour', g.next_closed_hour, 'UTC')
               OR g.next_closed_hour < g.admitted_hour
               OR (closed_target > g.admitted_hour AND g.next_closed_hour < closed_target)))
    ) THEN
        RAISE EXCEPTION 'Unfinished aggregate scheduling work remains; retain progress schema';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.jobs
        WHERE status IN ('queued', 'running')
          AND kind IN ('schedule_attribution_daily', 'refresh_attribution_daily', 'rollup_hourly')
    ) THEN
        RAISE EXCEPTION 'Aggregate scheduling jobs remain; drain before downgrade';
    END IF;

    -- Non-NULL closed_pending_since with a caught-up cursor, terminal/missing
    -- queued_key references, attempt tokens and completed outcomes do not block.
    PERFORM pg_catalog.set_config('app.bypass_rls', coalesce(previous_bypass, ''), true);
END
$guard$;

DROP TRIGGER projects_admit_aggregate_job_progress ON public.projects;
DROP FUNCTION public.admit_aggregate_job_progress();
DROP TABLE public.aggregate_job_progress;
DROP FUNCTION public.aggregate_job_progress_immutable();
DROP SEQUENCE public.aggregate_job_attempt_seq;
-- No CASCADE. Never alter/drop 1026 financial, 1027 coverage, 1028 dirty state.
    """)
