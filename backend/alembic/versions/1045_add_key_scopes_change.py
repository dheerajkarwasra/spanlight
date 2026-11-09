"""Allow the key.scopes_change audit action.

Revision ID: 1045
Revises: 1044
Create Date: 2026-11-08

As in 1041, replace the action CHECK as NOT VALID, then validate separately under
an autocommit block and a 5 s lock timeout. Existing keys are not changed.
Downgrade locks out audit writers before refusing if the new action exists: deleting
those events would break the append-only audit chain.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1045"
down_revision: str | None = "1044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
ACTION_CHECK = "audit_events_action_check"
NEW_AUDIT_ACTION = "key.scopes_change"

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
    "user.totp_recovery_regenerate",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, NEW_AUDIT_ACTION))
        op.execute(f"ALTER TABLE audit_events VALIDATE CONSTRAINT {ACTION_CHECK}")
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("LOCK TABLE audit_events IN SHARE ROW EXCLUSIVE MODE")
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM audit_events WHERE action = '{NEW_AUDIT_ACTION}') THEN
                RAISE EXCEPTION '{NEW_AUDIT_ACTION} audit events exist; retain migration 1045';
            END IF;
        END $$
    """)
    _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    """Replace the action CHECK with one over `actions`, `NOT VALID` (no row is scanned)."""
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute(
        f"ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS {ACTION_CHECK}, "
        f"ADD CONSTRAINT {ACTION_CHECK} CHECK (action IN ({listed})) NOT VALID"
    )
