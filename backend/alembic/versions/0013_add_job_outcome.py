"""Add the outcome of a finished job.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-09

`jobs.outcome` says how a job that ended `done` ended: `ok`, or one of the `skipped_*` values
when the task found an optional setting missing or a spending cap reached and did nothing. It
is NULL while the job has not finished, after a failure, and on rows that finished before this
column existed.

The CHECK lists the values `app.jobs.outcome.JobOutcome` defines; a new outcome needs a new
migration that widens it. The column is nullable with no default, so adding it rewrites nothing.

Downgrade drops the column (and its CHECK with it).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN outcome text")
    # Added NOT VALID, then validated on its own: validating takes a lighter lock than adding a
    # checked constraint, so a large jobs table is not locked against writes while it is scanned.
    op.execute(
        """
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_outcome_check
                CHECK (outcome IN ('ok', 'skipped_not_configured', 'skipped_budget')) NOT VALID
        """
    )
    _validate("jobs", "jobs_outcome_check")


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
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            ALTER TABLE {table} VALIDATE CONSTRAINT {constraint};
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_outcome_check, DROP COLUMN outcome")
