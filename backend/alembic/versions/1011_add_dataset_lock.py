"""Let admins lock a dataset, and record locking and unlocking in the audit log.

Revision ID: 1011
Revises: 1010
Create Date: 2026-10-20

`datasets.locked_at` is NULL while a dataset can be edited and holds the time it was locked
otherwise. A locked dataset refuses item changes, so the runs scored against it stay comparable;
an admin can unlock it.

Choices worth knowing about:

* The column is nullable with no default, so adding it changes only the catalog.
* `audit_events.action` is a CHECK over a fixed list, so it is dropped and added again with
  `dataset.lock` and `dataset.unlock`, as in 1001.

The migration is one transaction under a 5 s lock timeout. Downgrade deletes the audit events the
old CHECK rejects, restores it, and drops the column; which datasets were locked is lost.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1011"
down_revision: str | None = "1010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

# Every action 1001 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = ("dataset.lock", "dataset.unlock")


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE datasets ADD COLUMN IF NOT EXISTS locked_at timestamptz")
    _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    listed = ", ".join(f"'{action}'" for action in NEW_AUDIT_ACTIONS)
    op.execute(f"DELETE FROM audit_events WHERE action IN ({listed})")
    _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)
    op.execute("ALTER TABLE datasets DROP COLUMN IF EXISTS locked_at")


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check "
        f"CHECK (action IN ({listed}))"
    )
