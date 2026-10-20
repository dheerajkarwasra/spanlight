"""Harden the audit log: who, from where, with what outcome, append-only.

Revision ID: 1013
Revises: 1012
Create Date: 2026-10-20

`audit_events` gains the columns an industry audit event carries: the actor's type, the personal
access token used, the actor's email at the time, the project, the outcome and its reason, the user
agent and the request id. Rows written before this migration leave the new columns NULL (not
known), except `outcome`: every earlier event was a success, so its default is true for them.

Choices worth knowing about:

* Every column is nullable or has a constant default, so adding them changes only the catalog.
* `credential_id` and `project_id` have no foreign key: an event outlives the token and the
  project it names.
* A row trigger makes the table append-only. A foreign-key cascade still works (deleting an org
  deletes its log; deleting a user nulls `actor_user_id`), and so does the retention purge, which
  sets `spanlight.audit_purge` in its own transaction. The database owner can still edit rows;
  this stops application bugs, not a superuser.
* `audit_events.action` is a CHECK over a fixed list, so it is dropped and added again with the
  new actions, as in 1011.

The migration is one transaction under a 5 s lock timeout. Downgrade drops the trigger, deletes
the events the old CHECK rejects, restores it and drops the columns; what they held is lost.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1013"
down_revision: str | None = "1012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

# Every action 1011 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = (
    "user.login",
    "user.logout",
    "user.session_revoke",
    "token.create",
    "token.revoke",
    "export.create",
    "audit.export",
    "access.denied",
)

COLUMNS = (
    "actor_type text CHECK (actor_type IN ('user', 'token', 'system', 'cli'))",
    "credential_id uuid",
    "actor_email text",
    "project_id uuid",
    "outcome text NOT NULL DEFAULT 'success' CHECK (outcome IN ('success', 'failure'))",
    "reason text",
    "user_agent text",
    "request_id text",
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for column in COLUMNS:
        op.execute(f"ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS {column}")
    _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- A foreign-key cascade (an org or a user deleted) runs one trigger level down.
            IF pg_trigger_depth() > 1 THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF TG_OP = 'DELETE' AND current_setting('spanlight.audit_purge', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    listed = ", ".join(f"'{action}'" for action in NEW_AUDIT_ACTIONS)
    op.execute(f"DELETE FROM audit_events WHERE action IN ({listed})")
    _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)
    for column in COLUMNS:
        op.execute(f"ALTER TABLE audit_events DROP COLUMN IF EXISTS {column.split()[0]}")


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check "
        f"CHECK (action IN ({listed}))"
    )
