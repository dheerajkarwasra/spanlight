"""Add Doctor sensitivity: a per-project setting per check, and each insight's strongest reading.

Revision ID: 1018
Revises: 1017
Create Date: 2026-10-25

`detector_sensitivity` holds one row per project and check whose setting is not the default; no
row means High recall, the detectors' own rule. `insights.peak_strength` keeps the strongest
reading the check's gate compares (`app.insights.sensitivity.strength`), so the settings preview
can count stored findings at each level without replaying the detectors.

Choices worth knowing about:

* The key is `(project_id, detector)`: one setting per check. `detector` is plain text, not a
  foreign key: the checks are code (`app.insights.engine.DETECTORS`), and the application only
  writes the tunable ones. `level` is a CHECK over the three settings.
* `updated_by` is `ON DELETE SET NULL` (the setting outlives the admin who chose it) and is
  indexed so removing a user does not scan the table.
* Row-level security is enabled and forced with the `0505` project isolation and worker bypass
  expressions. The app role's privileges come from the schema's default privileges, as for every
  table (`app.db.roles`).
* `peak_strength` is nullable with no default, so adding it changes only the catalog; rows from
  before this migration stay NULL and the preview reads their stored evidence instead.
* `audit_events.action` is a CHECK over a fixed list, so it is dropped and added again with
  `insight.sensitivity_change`, as in 1014. Adding it validated scans `audit_events` once under
  the ACCESS EXCLUSIVE lock the drop takes, so audited writes (logins included) wait until the
  scan finishes; the migration stays one atomic transaction.

The migration is one transaction under a 5 s lock timeout.
Downgrade drops the table and the column, which loses every setting and peak. It restores the
previous audit action list as `NOT VALID` instead of deleting the `insight.sensitivity_change`
events: the audit log is append-only and hash-chained (1013, 1014), and deleting events would
break the chain. New writes are still held to the old list. A further downgrade past 1014
revalidates the list and fails while such events exist.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1018"
down_revision: str | None = "1017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"
TABLE = "detector_sensitivity"

# Every action 1014 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = ("insight.sensitivity_change",)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        """
        CREATE TABLE detector_sensitivity (
            project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            detector    text NOT NULL,
            level       text NOT NULL
                CONSTRAINT detector_sensitivity_level_check
                CHECK (level IN ('high_recall', 'balanced', 'high_precision')),
            updated_by  uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, detector)
        )
        """
    )
    op.execute(
        "CREATE INDEX detector_sensitivity_updated_by_idx ON detector_sensitivity (updated_by)"
    )
    _enable_rls(TABLE)
    op.execute("ALTER TABLE insights ADD COLUMN IF NOT EXISTS peak_strength numeric(20, 4)")
    _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _set_audit_actions(PREVIOUS_AUDIT_ACTIONS, valid=False)
    op.execute("ALTER TABLE insights DROP COLUMN IF EXISTS peak_strength")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_project_isolation ON {table} "
        f"USING ({PROJECT_EXPRESSION}) WITH CHECK ({PROJECT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY {table}_worker_bypass ON {table} "
        f"USING ({BYPASS_EXPRESSION}) WITH CHECK ({BYPASS_EXPRESSION})"
    )


def _set_audit_actions(actions: tuple[str, ...], *, valid: bool = True) -> None:
    """Replace the action CHECK; `valid=False` adds it `NOT VALID`, without scanning rows."""
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check "
        f"CHECK (action IN ({listed})){'' if valid else ' NOT VALID'}"
    )
