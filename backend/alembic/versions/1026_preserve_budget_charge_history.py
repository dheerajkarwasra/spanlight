"""Preserve period-bounded budget correlation and record missing spend history.

Revision ID: 1026
Revises: 1025

Existing projects cannot prove that historical telemetry was never deleted. Their current
period therefore fails closed until the next reset. Existing unattributed counter charges
remain a conservative carry; no financial amounts are reset or inferred from telemetry.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1026"
down_revision: str | None = "1025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("ALTER TABLE projects ADD COLUMN budget_history_missing_through timestamptz")
    op.execute("UPDATE projects SET budget_history_missing_through = now()")
    op.execute(
        "ALTER TABLE budget_counters ADD COLUMN legacy_unattributed_usd numeric(18,8) "
        "NOT NULL DEFAULT 0 CHECK (legacy_unattributed_usd >= 0)"
    )
    op.execute(
        "UPDATE budget_counters c SET legacy_unattributed_usd = greatest(0, settled_usd - "
        "(SELECT coalesce(sum(r.amount_usd), 0) FROM budget_reservations r "
        "WHERE r.budget_id = c.budget_id AND r.period_start = c.period_start "
        "AND r.status = 'reaped'))"
    )
    op.execute(
        "ALTER TABLE budget_reservations ADD COLUMN charged_usd numeric(18,8) "
        "CHECK (charged_usd >= 0)"
    )
    op.execute(
        "ALTER TABLE budget_reservations ADD COLUMN reclassified boolean NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE budget_reservations DROP CONSTRAINT budget_reservations_status_check")
    op.execute(
        "ALTER TABLE budget_reservations ADD CONSTRAINT budget_reservations_status_check "
        "CHECK (status IN ('held', 'reaped', 'settled'))"
    )
    op.execute(
        "ALTER TABLE budget_reservations ADD CONSTRAINT budget_reservations_charge_check "
        "CHECK ((status = 'settled') = (charged_usd IS NOT NULL))"
    )
    op.execute("""
        CREATE FUNCTION mark_missing_budget_history() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            UPDATE projects p
            SET budget_history_missing_through = greatest(p.budget_history_missing_through,
                                                          lost.last_started)
            FROM (SELECT d.project_id,
                         max(greatest(d.started_at, r.expires_at - interval '12 minutes'))
                             AS last_started
                  FROM deleted_spans d
                  LEFT JOIN budget_reservations r
                    ON r.project_id = d.project_id AND r.trace_id = d.trace_id
                   AND r.span_id = d.span_id
                  WHERE (NOT d.usage_rollup AND d.cost_usd > 0) OR r.charged_usd > 0
                  GROUP BY d.project_id) lost
            WHERE p.id = lost.project_id;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER spans_budget_history_deleted
        AFTER DELETE ON spans REFERENCING OLD TABLE AS deleted_spans
        FOR EACH STATEMENT EXECUTE FUNCTION mark_missing_budget_history()
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    # Old code deletes settled identities; retain the counter's money on downgrade.
    op.execute("DELETE FROM budget_reservations WHERE status = 'settled'")
    op.execute("DROP TRIGGER spans_budget_history_deleted ON spans")
    op.execute("DROP FUNCTION mark_missing_budget_history()")
    op.execute("ALTER TABLE projects DROP COLUMN budget_history_missing_through")
    op.execute("ALTER TABLE budget_counters DROP COLUMN legacy_unattributed_usd")
    op.execute("ALTER TABLE budget_reservations DROP CONSTRAINT budget_reservations_charge_check")
    op.execute("ALTER TABLE budget_reservations DROP COLUMN charged_usd")
    op.execute("ALTER TABLE budget_reservations DROP COLUMN reclassified")
    op.execute("ALTER TABLE budget_reservations DROP CONSTRAINT budget_reservations_status_check")
    op.execute(
        "ALTER TABLE budget_reservations ADD CONSTRAINT budget_reservations_status_check "
        "CHECK (status IN ('held', 'reaped'))"
    )
