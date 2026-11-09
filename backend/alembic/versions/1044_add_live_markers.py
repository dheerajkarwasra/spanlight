"""Read only the requested projects' aggregate change markers across RLS.

Revision ID: 1044
Revises: 1043
Create Date: 2026-11-04

Like 0507, this SECURITY DEFINER function belongs to the migration role, pins
search_path and uses schema-qualified tables. The caller's bypass setting is
restored on success and on error. PUBLIC cannot execute it; ensure-app-role
grants its signature through APP_FUNCTIONS.

The pair (row count, generation sum) detects lower generations arriving after
higher ones and repair deletions. UNION ALL preserves both legacy and shard rows.
Only the authorized marker reader may call it with subscribed project IDs.

This adds one function without rewriting data. Downgrade drops it; roll back
the application release that calls it first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1044"
down_revision: str | None = "1043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "public.live_markers(ids uuid[])"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {SIGNATURE}
        RETURNS TABLE (project_id uuid, row_count bigint, generation_sum numeric)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            caller_bypass text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            IF cardinality(ids) > 10000 THEN
                RAISE EXCEPTION 'live_markers accepts at most 10000 project ids';
            END IF;
            RETURN QUERY
            SELECT m.project_id, count(*), sum(m.generation)
            FROM (
                SELECT h.project_id, h.generation
                FROM public.aggregate_dirty_hours AS h
                WHERE h.project_id = ANY(ids)
                UNION ALL
                SELECT s.project_id, s.generation
                FROM public.aggregate_dirty_shards AS s
                WHERE s.project_id = ANY(ids)
            ) AS m
            GROUP BY m.project_id;
            PERFORM set_config('app.bypass_rls', COALESCE(caller_bypass, ''), true);
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config('app.bypass_rls', COALESCE(caller_bypass, ''), true);
            RAISE;
        END
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.live_markers(uuid[])")
