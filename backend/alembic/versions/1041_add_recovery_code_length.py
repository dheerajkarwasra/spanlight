"""Add `recovery_codes.code_length`, the format of each stored recovery code, and the
`user.totp_recovery_regenerate` audit action.

Revision ID: 1041
Revises: 1040
Create Date: 2026-11-03

`code_length` is the length of the code a row stores: 10 for the legacy format, 28 for the
current one. Rows written before this revision stay NULL, which also means legacy; there is no
backfill. A server that does not know the column inserts NULL, which is right because such a
server issues 10-character codes. The nullable column has no default, so adding it changes only
the catalog and rewrites no rows. The table is not project-scoped, so no RLS policy changes.

The `code_length` CHECK and the widened `audit_events.action` CHECK (as 1030 widens it) are added
`NOT VALID` (a catalog change under a lock held for milliseconds) and validated in a statement of
their own, which scans the table under SHARE UPDATE EXCLUSIVE and lets writes continue. Every
statement commits on its own (`autocommit_block()`) under a 5 s lock timeout, and every step is
safe to repeat.

Downgrade runs in one transaction. It first locks `recovery_codes` and `audit_events` in SHARE
ROW EXCLUSIVE mode (under the same lock timeout), so no writer can add a 28-character code or a
regenerate event after the check, then refuses if either exists: a 28-character code would lose
its format marker and lock its owner out on a server that only knows 10-character codes, and an
audit event cannot be deleted without breaking the hash chain (the append-only trigger of 1013
refuses it). Otherwise it drops the column and restores 1030's action list `NOT VALID`, as 1030's
own downgrade does; the check just proved no row violates it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1041"
down_revision: str | None = "1040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
LENGTH_CHECK = "ck_recovery_codes_code_length"
ACTION_CHECK = "audit_events_action_check"
NEW_AUDIT_ACTION = "user.totp_recovery_regenerate"

# Every action 1030 allows, in its order.
PREVIOUS_AUDIT_ACTIONS = (
    "org.create",
    "member.add",
    "member.role_change",
    "member.remove",
    "invite.create",
    "invite.revoke",
    "invite.accept",
    "project.create",
    "project.update",
    "key.create",
    "key.revoke",
    "user.password_reset",
    "user.oauth_link",
    "user.oauth_unlink",
    "user.totp_enable",
    "user.totp_disable",
    "org.update",
    "project.delete",
    "credential.create",
    "credential.rotate",
    "credential.delete",
    "gateway_route.create",
    "gateway_route.update",
    "gateway_route.revert",
    "gateway_route.delete",
    "gateway_key.create",
    "gateway_key.update",
    "gateway_key.revoke",
    "fault_profile.create",
    "fault_profile.update",
    "fault_profile.delete",
    "price_override.create",
    "price_override.delete",
    "gateway_cache.purge",
    "alert_channel.create",
    "alert_channel.update",
    "alert_channel.delete",
    "alert_channel.test",
    "alert_channel.retry_delivery",
    "alert_rule.create",
    "alert_rule.update",
    "alert_rule.delete",
    "alert_rule.mute",
    "alert_event.acknowledge",
    "budget.create",
    "budget.update",
    "budget.delete",
    "insight.acknowledge",
    "insight.resolve",
    "insight.mute",
    "insight.unmute",
    "insight.explain",
    "prompt.create",
    "prompt.version_create",
    "prompt.label_set",
    "prompt.label_delete",
    "prompt.delete",
    "score_config.create",
    "score_config.update",
    "score_config.delete",
    "dataset.create",
    "dataset.delete",
    "annotation_queue.create",
    "annotation_queue.delete",
    "evaluator.create",
    "evaluator.update",
    "evaluator.delete",
    "experiment.create",
    "experiment.run",
    "billing_source.create",
    "billing_source.update",
    "billing_source.rotate",
    "billing_source.delete",
    "billing_source.refresh",
    "billing_source.upload",
    "review_policy.create",
    "review_policy.update",
    "review_policy.delete",
    "review_policy.run",
    "replay.run",
    "dataset.lock",
    "dataset.unlock",
    "user.login",
    "user.logout",
    "user.session_revoke",
    "token.create",
    "token.revoke",
    "export.create",
    "audit.export",
    "access.denied",
    "audit_stream.create",
    "audit_stream.update",
    "audit_stream.delete",
    "audit_stream.rotate_secret",
    "audit_stream.test",
    "insight.sensitivity_change",
    "end_user.erase_request",
    "end_user.erase",
    "experiment.schedule_change",
    "user.terms_accept",
    "user.update",
    "user.email_change",
    "user.delete",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE recovery_codes ADD COLUMN IF NOT EXISTS code_length smallint NULL")
        op.execute(
            f"ALTER TABLE recovery_codes DROP CONSTRAINT IF EXISTS {LENGTH_CHECK}, "
            f"ADD CONSTRAINT {LENGTH_CHECK} CHECK (code_length IN (10, 28)) NOT VALID"
        )
        op.execute(f"ALTER TABLE recovery_codes VALIDATE CONSTRAINT {LENGTH_CHECK}")
        _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, NEW_AUDIT_ACTION))
        op.execute(f"ALTER TABLE audit_events VALIDATE CONSTRAINT {ACTION_CHECK}")
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    # Lock before checking: see the module docstring.
    op.execute("LOCK TABLE recovery_codes, audit_events IN SHARE ROW EXCLUSIVE MODE")
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM recovery_codes WHERE code_length = 28) THEN
                RAISE EXCEPTION '28-character recovery codes exist; retain migration 1041';
            END IF;
            IF EXISTS (SELECT 1 FROM audit_events WHERE action = '{NEW_AUDIT_ACTION}') THEN
                RAISE EXCEPTION '{NEW_AUDIT_ACTION} audit events exist; retain migration 1041';
            END IF;
        END $$
    """)
    op.execute(f"ALTER TABLE recovery_codes DROP CONSTRAINT IF EXISTS {LENGTH_CHECK}")
    op.execute("ALTER TABLE recovery_codes DROP COLUMN IF EXISTS code_length")
    _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    """Replace the action CHECK with one over `actions`, `NOT VALID` (no row is scanned)."""
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute(
        f"ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS {ACTION_CHECK}, "
        f"ADD CONSTRAINT {ACTION_CHECK} CHECK (action IN ({listed})) NOT VALID"
    )
