"""Add weekly experiment schedules: `experiments.schedule`, `experiment_runs.trigger` and the
`experiment.schedule_change` audit action.

Revision ID: 1025
Revises: 1024
Create Date: 2026-10-26

`experiments.schedule` is `weekly` or NULL (not scheduled); the daily `schedule_experiments` job
starts one run per scheduled experiment per ISO week. `experiment_runs.trigger` tells a scheduled
run (`schedule`) from one a person started (`manual`): `started_by` cannot, because it is set to
NULL when the user is deleted. Both defaults are constants, so on PostgreSQL 11+ the columns
change only the catalog and rewrite no rows. Both tables already have row-level security; a new
column needs no policy change.

`audit_events.action` is a CHECK over a fixed list, so it is dropped and added again with the new
action, as 1024 does: added `NOT VALID` (a catalog change under a lock held for milliseconds) and
validated in a statement of its own, which scans the table under SHARE UPDATE EXCLUSIVE and lets
writes continue. `jobs.outcome` gains `skipped_run_in_progress` (a scheduled rerun that found a
run of its experiment already active) the same way, as 1003 widened it. Every statement commits
on its own (`autocommit_block()`) under a 5 s lock timeout.

Downgrade drops both columns and restores the previous action list as `NOT VALID` instead of
deleting the new events: deleting would break the hash chain (the append-only trigger of 1013
refuses it). New writes are held to the old list. Jobs holding the new outcome are cleared
first, as in 1003.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1025"
down_revision: str | None = "1024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

# Every action 1024 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = ("experiment.schedule_change",)
# Every outcome 1003 allows, and the new one.
PREVIOUS_OUTCOMES = ("ok", "skipped_not_configured", "skipped_budget", "already_selected")
NEW_OUTCOME = "skipped_run_in_progress"


def upgrade() -> None:
    # Outside the migration's transaction (`autocommit_block()`), each statement committing on
    # its own, so the validating scans hold no ACCESS EXCLUSIVE lock. Every step is idempotent,
    # so an interrupted run is simply run again.
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE experiments ADD COLUMN IF NOT EXISTS schedule text NULL "
            "CONSTRAINT experiments_schedule_check CHECK (schedule IN ('weekly'))"
        )
        op.execute(
            "ALTER TABLE experiment_runs ADD COLUMN IF NOT EXISTS trigger text NOT NULL "
            "DEFAULT 'manual' CONSTRAINT experiment_runs_trigger_check "
            "CHECK (trigger IN ('manual', 'schedule'))"
        )
        _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))
        op.execute("ALTER TABLE audit_events VALIDATE CONSTRAINT audit_events_action_check")
        _set_outcomes((*PREVIOUS_OUTCOMES, NEW_OUTCOME))
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE experiment_runs DROP COLUMN IF EXISTS trigger")
        op.execute("ALTER TABLE experiments DROP COLUMN IF EXISTS schedule")
        # Left NOT VALID: see the module docstring.
        _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)
        op.execute(f"UPDATE jobs SET outcome = NULL WHERE outcome = '{NEW_OUTCOME}'")
        _set_outcomes(PREVIOUS_OUTCOMES)
        op.execute("RESET lock_timeout")


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    """Replace the action CHECK, added `NOT VALID` so no row is scanned under the lock."""
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_action_check")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check "
        f"CHECK (action IN ({listed})) NOT VALID"
    )


def _set_outcomes(values: tuple[str, ...]) -> None:
    """Replace the outcome CHECK: added `NOT VALID`, then validated in its own statement."""
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_outcome_check")
    op.execute(
        f"ALTER TABLE jobs ADD CONSTRAINT jobs_outcome_check "
        f"CHECK (outcome IN ({allowed})) NOT VALID"
    )
    op.execute("ALTER TABLE jobs VALIDATE CONSTRAINT jobs_outcome_check")
