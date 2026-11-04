"""Add terms acceptance to `users`, the `email_change` token kind with its `new_email`, and the
four account audit actions.

Revision ID: 1030
Revises: 1029
Create Date: 2026-11-01

`users.terms_version` is the version of the hosted terms the user accepted (the server's
`TERMS_VERSION` when they did) and `users.terms_accepted_at` when; both NULL means no version was
accepted. They are one fact, so `users_terms_pair_check` refuses one without the other. An
`email_change` token carries the address it moves the user to in `email_tokens.new_email`. A
CHECK that `new_email` is set exactly when `kind = 'email_change'` is not added: a new enum value
cannot be used in the transaction that adds it, and the app writes both together anyway. The
nullable columns have no default, so they change only the catalog and rewrite no rows. Neither
table is project-scoped, so no RLS policy changes.

The terms CHECK and the widened `audit_events.action` CHECK (as 1024 and 1025 widen it) are added
`NOT VALID` (a catalog change under a lock held for milliseconds) and validated in a statement of
their own, which scans the table under SHARE UPDATE EXCLUSIVE and lets writes continue. `ALTER
TYPE ... ADD VALUE` cannot run inside a transaction block. Every statement therefore commits on its
own (`autocommit_block()`) under a 5 s lock timeout, and every step is safe to repeat.

Downgrade drops the terms CHECK and the three columns, and restores the previous action list as
`NOT VALID` instead of deleting the new events: deleting would break the hash chain (the
append-only trigger of 1013 refuses it). It leaves `email_change` in `email_token_kind`, because
Postgres cannot drop an enum value; with `new_email` gone the app at 1029 never writes that kind,
and `ADD VALUE IF NOT EXISTS` makes the next upgrade a no-op for it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1030"
down_revision: str | None = "1029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
TERMS_CHECK = "users_terms_pair_check"
ACTION_CHECK = "audit_events_action_check"

# Every action 1025 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = ("user.terms_accept", "user.update", "user.email_change", "user.delete")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_version text NULL")
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at timestamptz NULL")
        op.execute(
            f"ALTER TABLE users DROP CONSTRAINT IF EXISTS {TERMS_CHECK}, "
            f"ADD CONSTRAINT {TERMS_CHECK} "
            "CHECK ((terms_version IS NULL) = (terms_accepted_at IS NULL)) NOT VALID"
        )
        op.execute(f"ALTER TABLE users VALIDATE CONSTRAINT {TERMS_CHECK}")
        op.execute("ALTER TABLE email_tokens ADD COLUMN IF NOT EXISTS new_email citext NULL")
        op.execute("ALTER TYPE email_token_kind ADD VALUE IF NOT EXISTS 'email_change'")
        _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))
        op.execute(f"ALTER TABLE audit_events VALIDATE CONSTRAINT {ACTION_CHECK}")
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        # Left NOT VALID, and `email_change` left in the enum: see the module docstring.
        _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)
        op.execute("ALTER TABLE email_tokens DROP COLUMN IF EXISTS new_email")
        op.execute(f"ALTER TABLE users DROP CONSTRAINT IF EXISTS {TERMS_CHECK}")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS terms_accepted_at")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS terms_version")
        op.execute("RESET lock_timeout")


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    """Replace the action CHECK with one over `actions`, `NOT VALID` (no row is scanned)."""
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute(
        f"ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS {ACTION_CHECK}, "
        f"ADD CONSTRAINT {ACTION_CHECK} CHECK (action IN ({listed})) NOT VALID"
    )
