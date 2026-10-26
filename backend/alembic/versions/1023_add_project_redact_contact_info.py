"""Add the per-project switch that redacts email addresses and phone numbers in captured spans.

Revision ID: 1023
Revises: 1022
Create Date: 2026-10-26

`projects.redact_contact_info` turns on the contact patterns of `app.core.redact` (emails and
`+`-prefixed international phone numbers) for spans ingested while it is on, on top of the secret
patterns every project gets. Off by default: code-heavy payloads keep strings like
`git@github.com` unless a project asks. `projects` already has its access rules, and a new column
needs no policy change.

The default is a constant, so on PostgreSQL 11+ adding the NOT NULL column changes only the
catalog and rewrites no rows. One transaction under a 5 s lock timeout. Downgrade drops the
column: every project goes back to secret redaction only; spans already stored stay redacted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1023"
down_revision: str | None = "1022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS redact_contact_info boolean "
        "NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS redact_contact_info")
