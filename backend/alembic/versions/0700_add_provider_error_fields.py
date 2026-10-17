"""Add the provider's error fields to spans, the `gateway` error class, and trace retry columns.

Revision ID: 0700
Revises: 0602
Create Date: 2026-10-16

A failed call now keeps what the provider said about it: `spans.provider_error_type` and
`spans.provider_error_code` (the `type` and `code` of the provider's error envelope, from the
`provider.error.type` and `provider.error.code` attributes) and `spans.rate_limit_kind`, which
names the capacity a refusal ran into (`requests`, `tokens`, `spend_cap`, `acceleration`,
`overloaded`, `gateway` or `unknown`). All three stay NULL when nothing was reported.

`spans.error_class` gains `gateway`: Spanlight's gateway answered without a provider verdict and
without a caller mistake. The 0400 CHECK is replaced by the same rule with one more value.

`traces.retry_calls` counts the trace's superseded llm calls (a request repeated with the same
request hash) and `traces.retry_cost_usd` sums their cost. Both stay NULL for traces not
recomputed since this migration: unknown, never 0.

Choices worth knowing about:

* Every step runs outside the migration's transaction (`autocommit_block()`), each statement
  committing on its own, as 0400 and 0508 do for `spans`. The nullable column adds and the
  `NOT VALID` constraints change only the catalog, under an ACCESS EXCLUSIVE lock held for
  milliseconds; the migration waits at most 5 seconds for it (`lock_timeout`) rather than stall
  every ingest queued behind the wait. Each `VALIDATE CONSTRAINT` then scans the table in its own
  statement under SHARE UPDATE EXCLUSIVE, which lets ingestion and reads continue.
* Every step is safe to repeat (`IF NOT EXISTS`, drop-then-add of the constraints), so running
  the migration again after an interrupted run is safe. The error-class CHECK is dropped and
  added back in one `ALTER TABLE`, in both directions, so a lock timeout between the two can
  never leave `spans` without it.
* No index is added: the provider health reads go through `spans_project_error_class_started_idx`.

Downgrade is lossy for one class: the spans whose `error_class` is `gateway` are deleted (with
the worker bypass, so every project's rows are reached), because the 0400 CHECK it restores
refuses them. It then drops the five columns and puts the 0400 CHECK back, validated.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0700"
down_revision: str | None = "0602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
ERROR_CLASS_CHECK = "spans_error_class_check"
RATE_LIMIT_KIND_CHECK = "spans_rate_limit_kind_check"
ERROR_CLASSES_0400 = (
    "auth",
    "rate_limit",
    "timeout",
    "context_length",
    "content_filter",
    "provider_5xx",
    "network",
    "client",
    "unknown",
)
ERROR_CLASSES = (*ERROR_CLASSES_0400, "gateway")
RATE_LIMIT_KINDS = (
    "requests",
    "tokens",
    "spend_cap",
    "acceleration",
    "overloaded",
    "gateway",
    "unknown",
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _error_class_check(values: tuple[str, ...]) -> str:
    # The wording of 0400, so a downgrade restores exactly the constraint 0400 wrote.
    return f"error_class IS NULL OR (status = 'error' AND error_class IN ({_in(values)}))"


def upgrade() -> None:
    rate_limit_kind = f"rate_limit_kind IS NULL OR rate_limit_kind IN ({_in(RATE_LIMIT_KINDS)})"
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE spans ADD COLUMN IF NOT EXISTS provider_error_type text, "
            "ADD COLUMN IF NOT EXISTS provider_error_code text, "
            "ADD COLUMN IF NOT EXISTS rate_limit_kind text"
        )
        # One statement, so no committed state ever leaves spans without an error-class CHECK.
        op.execute(
            f"ALTER TABLE spans DROP CONSTRAINT IF EXISTS {RATE_LIMIT_KIND_CHECK}, "
            f"DROP CONSTRAINT IF EXISTS {ERROR_CLASS_CHECK}, "
            f"ADD CONSTRAINT {RATE_LIMIT_KIND_CHECK} CHECK ({rate_limit_kind}) NOT VALID, "
            f"ADD CONSTRAINT {ERROR_CLASS_CHECK} CHECK ({_error_class_check(ERROR_CLASSES)}) "
            "NOT VALID"
        )
        op.execute(
            "ALTER TABLE traces ADD COLUMN IF NOT EXISTS retry_calls integer, "
            "ADD COLUMN IF NOT EXISTS retry_cost_usd numeric(14, 8)"
        )
        op.execute("RESET lock_timeout")
        _validate("spans", RATE_LIMIT_KIND_CHECK)
        _validate("spans", ERROR_CLASS_CHECK)


# Copied on purpose into each migration that validates: migrations do not import app code or
# each other, so a later change to a shared helper cannot rewrite what a shipped one did.
def _validate(table: str, constraint: str) -> None:
    """Validate `constraint` against every row of `table`, whatever row-level security hides.

    Migrations run as the table owner, and FORCE ROW LEVEL SECURITY applies the policies to it:
    Postgres validates a foreign key with a query that sees only the rows the policies let
    through. (A CHECK is validated by scanning the table, which the policies do not filter; it
    goes through here too, so every validation follows one rule.) The worker bypass is set for
    the statement's own transaction and the caller's value restored afterwards; on an error the
    transaction aborts and discards the setting by itself. One `DO` block, so the setting and the
    validation share a transaction even inside `autocommit_block()`.
    """
    _with_bypass(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint};")


def _with_bypass(statement: str) -> None:
    """Run `statement` in one `DO` block with the worker bypass set for its transaction only."""
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            {statement}
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        # Lossy by design: the restored 0400 CHECK refuses this class.
        _with_bypass("DELETE FROM spans WHERE error_class = 'gateway';")
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            f"ALTER TABLE spans DROP CONSTRAINT IF EXISTS {RATE_LIMIT_KIND_CHECK}, "
            f"DROP CONSTRAINT IF EXISTS {ERROR_CLASS_CHECK}, "
            "DROP COLUMN IF EXISTS rate_limit_kind, "
            "DROP COLUMN IF EXISTS provider_error_code, "
            "DROP COLUMN IF EXISTS provider_error_type, "
            f"ADD CONSTRAINT {ERROR_CLASS_CHECK} "
            f"CHECK ({_error_class_check(ERROR_CLASSES_0400)}) NOT VALID"
        )
        op.execute(
            "ALTER TABLE traces DROP COLUMN IF EXISTS retry_cost_usd, "
            "DROP COLUMN IF EXISTS retry_calls"
        )
        op.execute("RESET lock_timeout")
        _validate("spans", ERROR_CLASS_CHECK)
