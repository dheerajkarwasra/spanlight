"""Add `retention_progress`: one keyset cursor row per retention cleanup class.

Revision ID: 1035
Revises: 1034
Create Date: 2026-11-02

The retention job used to drain every project's expired rows, class after class, in one run, so
a project with a large backlog or a locked one could keep every later project and class from
being visited before the job's time ran out. The job now takes one bounded batch per opportunity
and rotates. `retention_progress` holds one row per fixed cleanup class: the ordering token of
its last opportunity (`attempt_order`, from `retention_attempt_order_seq`, so the class served
longest ago goes next), the project or billing source visited last (`after_scope_id`, a keyset
boundary with deliberately no foreign key: deleting that project must not restart the scan) and
when. The two global classes (`provider_incidents`, `audit_events`) have no scope. These rows
record opportunities, never that anything was deleted.

Global scheduling metadata, like `jobs`: no tenant column and no row-level security. The app
role reaches it through the table and sequence grants `ensure_app_role` installs (its default
privileges when migrations run as the same owner, or the next `ensure-app-role` run otherwise);
PUBLIC's rights are revoked. The seeds have order 0 and the sequence starts at 1, so the first
opportunity orders after every seed; the sequence never cycles.

Downgrade drops both objects. It loses only the rotation order (the next upgrade starts a fresh
rotation), never source-loss, dirty or financial evidence. Stop the worker before either
direction so no retention run holds a cursor row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1035"
down_revision: str | None = "1034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("""
CREATE SEQUENCE public.retention_attempt_order_seq
    AS bigint INCREMENT BY 1 MINVALUE 1 NO MAXVALUE START WITH 1 CACHE 1 NO CYCLE;

CREATE TABLE public.retention_progress (
    class text PRIMARY KEY,
    attempt_order bigint NOT NULL,
    after_scope_id uuid,
    last_attempted_at timestamptz,
    CONSTRAINT retention_progress_class_check CHECK (class IN (
        'trace_cascades', 'hourly_aggregates', 'resolved_insights',
        'detector_runs', 'user_stats', 'daily_cost', 'daily_attribution',
        'scores', 'online_evaluator_runs', 'annotation_queue_items',
        'provider_cost_lines', 'fetch_runs', 'provider_incidents', 'audit_events'
    )),
    CONSTRAINT retention_progress_attempt_order_check CHECK (attempt_order >= 0),
    CONSTRAINT retention_progress_singleton_cursor_check CHECK (
        class NOT IN ('provider_incidents', 'audit_events') OR after_scope_id IS NULL
    )
);

CREATE INDEX retention_progress_attempt_order_idx
    ON public.retention_progress (attempt_order, class);

INSERT INTO public.retention_progress (class, attempt_order) VALUES
    ('trace_cascades', 0), ('hourly_aggregates', 0), ('resolved_insights', 0),
    ('detector_runs', 0), ('user_stats', 0), ('daily_cost', 0),
    ('daily_attribution', 0), ('scores', 0), ('online_evaluator_runs', 0),
    ('annotation_queue_items', 0), ('provider_cost_lines', 0), ('fetch_runs', 0),
    ('provider_incidents', 0), ('audit_events', 0);

REVOKE ALL ON TABLE public.retention_progress FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.retention_attempt_order_seq FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    # No CASCADE: nothing else may depend on scheduling metadata.
    op.execute("DROP TABLE public.retention_progress")
    op.execute("DROP SEQUENCE public.retention_attempt_order_seq")
