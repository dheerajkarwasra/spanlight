"""Make the audit log tamper-evident and add audit streaming.

Revision ID: 1014
Revises: 1013
Create Date: 2026-10-20

Every audit event gets a place in its organization's hash chain: `seq` (1, 2, 3, ... per org),
`prev_hash` (the previous event's `row_hash`) and `row_hash` (SHA-256 over the event's canonical
JSON, `prev_hash` included). A `BEFORE INSERT` trigger fills them, so every writer is covered, and
it takes a transaction-level advisory lock per organization first, so two transactions cannot
both extend the chain from the same event and `seq` follows commit order. Changing, adding or
removing an event in the middle breaks the chain where it happened; `audit_event_digest` is the
one definition of the hash, used by the trigger and by the check.

Choices worth knowing about:

* The canonical JSON is `jsonb_build_object(...)::text`: jsonb's text form orders keys and spacing
  the same way every time, and `created_at` is written as UTC with microseconds.
* `actor_user_id` loses its foreign key. `ON DELETE SET NULL` would rewrite a hashed column when a
  user is deleted and break the chain; the event keeps the id and `actor_email` as written.
* Events written before this migration are chained here, per organization in `(created_at, id)`
  order, with the append-only trigger off for the duration. They are attested as stored today.
* `audit_streams` holds at most one HTTPS destination per organization. `delivered_seq` is the
  last `seq` the receiver accepted, so delivery resumes exactly where it stopped.
* The table is small (admin activity), so the backfill and the unique index run in this
  migration's one transaction, under a 5 s lock timeout.

Downgrade drops the stream table, the chain columns, functions and trigger, deletes the events of
the new actions, restores the CHECK and puts back the foreign key (an id whose user is gone becomes
NULL first). The chain and every stream configuration are lost.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1014"
down_revision: str | None = "1013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

# Every action 1013 allows, in its order.
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
)
NEW_AUDIT_ACTIONS = (
    "audit_stream.create",
    "audit_stream.update",
    "audit_stream.delete",
    "audit_stream.rotate_secret",
    "audit_stream.test",
)

DIGEST = """
CREATE FUNCTION audit_event_digest(e audit_events) RETURNS bytea
LANGUAGE sql STABLE AS $$
    SELECT sha256(convert_to(jsonb_build_object(
        'seq', e.seq,
        'id', e.id,
        'org_id', e.org_id,
        'created_at', to_char(e.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'actor_user_id', e.actor_user_id,
        'actor_type', e.actor_type,
        'actor_email', e.actor_email,
        'credential_id', e.credential_id,
        'project_id', e.project_id,
        'action', e.action,
        'target_type', e.target_type,
        'target_id', e.target_id,
        'outcome', e.outcome,
        'reason', e.reason,
        'ip', e.ip::text,
        'user_agent', e.user_agent,
        'request_id', e.request_id,
        'metadata', e.metadata,
        'prev_hash', encode(e.prev_hash, 'hex')
    )::text, 'UTF8'))
$$
"""

CHAIN = """
CREATE FUNCTION audit_events_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    last_seq bigint;
    last_hash bytea;
BEGIN
    -- Held until commit: the next event of this org waits, then reads this one as its parent.
    PERFORM pg_advisory_xact_lock(hashtextextended('audit_events:' || NEW.org_id::text, 0));
    SELECT seq, row_hash INTO last_seq, last_hash
    FROM audit_events WHERE org_id = NEW.org_id AND seq IS NOT NULL
    ORDER BY seq DESC LIMIT 1;
    NEW.seq := coalesce(last_seq, 0) + 1;
    NEW.prev_hash := last_hash;
    NEW.row_hash := audit_event_digest(NEW);
    RETURN NEW;
END
$$
"""

BACKFILL = """
DO $$
DECLARE
    event audit_events;
    last_org uuid;
    next_seq bigint;
    last_hash bytea;
BEGIN
    FOR event IN SELECT * FROM audit_events ORDER BY org_id, created_at, id LOOP
        IF last_org IS DISTINCT FROM event.org_id THEN
            last_org := event.org_id;
            next_seq := 0;
            last_hash := NULL;
        END IF;
        next_seq := next_seq + 1;
        event.seq := next_seq;
        event.prev_hash := last_hash;
        event.row_hash := audit_event_digest(event);
        UPDATE audit_events
        SET seq = event.seq, prev_hash = event.prev_hash, row_hash = event.row_hash
        WHERE id = event.id;
        last_hash := event.row_hash;
    END LOOP;
END
$$
"""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_actor_user_id_fkey")
    for column in ("seq bigint", "prev_hash bytea", "row_hash bytea"):
        op.execute(f"ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS {column}")
    _set_audit_actions((*PREVIOUS_AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))
    op.execute(DIGEST)
    op.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only")
    op.execute(BACKFILL)
    op.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only")
    op.execute("CREATE UNIQUE INDEX audit_events_org_seq_idx ON audit_events (org_id, seq)")
    op.execute(CHAIN)
    op.execute(
        "CREATE TRIGGER audit_events_chain BEFORE INSERT ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_chain()"
    )
    op.execute(
        """
        CREATE TABLE audit_streams (
            org_id               uuid PRIMARY KEY REFERENCES organizations (id) ON DELETE CASCADE,
            url                  text NOT NULL,
            secret_enc           bytea NOT NULL,
            secret_key_id        text NOT NULL,
            enabled              boolean NOT NULL DEFAULT true,
            delivered_seq        bigint NOT NULL DEFAULT 0 CHECK (delivered_seq >= 0),
            last_delivered_at    timestamptz,
            last_error           text,
            last_error_at        timestamptz,
            consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
            created_by           uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("DROP TABLE IF EXISTS audit_streams")
    op.execute("DROP TRIGGER IF EXISTS audit_events_chain ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_chain()")
    op.execute("DROP INDEX IF EXISTS audit_events_org_seq_idx")
    op.execute("DROP FUNCTION IF EXISTS audit_event_digest(audit_events)")
    op.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only")
    listed = ", ".join(f"'{action}'" for action in NEW_AUDIT_ACTIONS)
    op.execute(f"DELETE FROM audit_events WHERE action IN ({listed})")
    op.execute(
        "UPDATE audit_events SET actor_user_id = NULL WHERE actor_user_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = audit_events.actor_user_id)"
    )
    op.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only")
    _set_audit_actions(PREVIOUS_AUDIT_ACTIONS)
    for column in ("seq", "prev_hash", "row_hash"):
        op.execute(f"ALTER TABLE audit_events DROP COLUMN IF EXISTS {column}")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_actor_user_id_fkey "
        "FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL"
    )


def _set_audit_actions(actions: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check "
        f"CHECK (action IN ({listed}))"
    )
