"""Add `alert_states.clear_since`: when a firing rule's value first read clear in its recovery
window.

Revision ID: 1036
Revises: 1035
Create Date: 2026-11-02

A firing rule now resolves only once its value has read clear for three consecutive minute passes
(`app.alerts.evaluate`), so the start of that window must survive from one pass to the next and
across worker restarts. It is NULL whenever the rule is not firing or its last known value
breached.

A nullable column with no default changes only the catalog, so the ALTER rewrites no rows and
holds its lock for milliseconds. The table's row-level security policy and its grants are
table-wide and cover the new column. The app at 1035 never reads or writes the column, so both
versions can run during a rolling deploy; a rule the old code resolves and fires again in that
window can keep a stale `clear_since`, which at worst resolves that one alert on its first clear
pass, as 1035 always did.

Downgrade drops the column: every open recovery window is forgotten, and the rules it belonged
to stay firing until the 1035 code sees their next clear pass.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1036"
down_revision: str | None = "1035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE alert_states ADD COLUMN IF NOT EXISTS clear_since timestamptz")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE alert_states DROP COLUMN IF EXISTS clear_since")
