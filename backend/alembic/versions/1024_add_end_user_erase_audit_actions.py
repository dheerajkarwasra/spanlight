"""Add the two audit actions of erasing one end user's data.

Revision ID: 1024
Revises: 1023
Create Date: 2026-10-26

`end_user.erase_request` is written when an admin asks for an erasure and `end_user.erase` when
the worker has finished it (`app.end_users.erasure_job`). Both carry a keyed hash of the end
user's id as the target, never the id itself: the audit log is append-only and hash-chained
(1013, 1014), so an id written there could never be erased.

`audit_events.action` is a CHECK over a fixed list, so it is dropped and added again with the
two actions. The new CHECK is added `NOT VALID` (catalog only, an ACCESS EXCLUSIVE lock held for
milliseconds) and validated in its own statement, which scans the table under SHARE UPDATE
EXCLUSIVE, so audited writes (logins included) are not held while the scan runs; each step runs
outside the migration's transaction (`autocommit_block()`, as 1010 does) under a 5 s lock
timeout. Every step is safe to repeat. No new table, so no new RLS policy.

Downgrade restores the previous list as `NOT VALID` and does not delete the new events: deleting
would break the hash chain (1014) and the append-only trigger (1013) refuses it. New writes are
held to the old list; a further downgrade past 1014 revalidates and fails while such events
exist.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1024"
down_revision: str | None = "1023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
CONSTRAINT = "audit_events_action_check"

# Every action 1018 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = ("end_user.erase_request", "end_user.erase")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))
        op.execute(f"ALTER TABLE audit_events VALIDATE CONSTRAINT {CONSTRAINT}")
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)
        op.execute("RESET lock_timeout")


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    """Replace the action CHECK with one over `actions`, `NOT VALID` (no row is scanned)."""
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute(
        f"ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS {CONSTRAINT}, "
        f"ADD CONSTRAINT {CONSTRAINT} CHECK (action IN ({listed})) NOT VALID"
    )
