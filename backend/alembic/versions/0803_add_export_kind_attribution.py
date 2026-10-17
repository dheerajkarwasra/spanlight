"""Add the `attribution` export kind.

Revision ID: 0803
Revises: 0802
Create Date: 2026-10-16

The attribution view's CSV is written by the exports job, so an export row now says whether it
holds traces or attribution groups. `exports.kind` is the `export_kind` enum of 0014 (one value,
`traces`, and the column's default), so the value is added to it.

Choices worth knowing about:

* `ALTER TYPE ... ADD VALUE` runs outside the migration's transaction (`autocommit_block()`): a
  value added inside a transaction cannot be used until it commits, and outside one the
  statement is safe to repeat (`IF NOT EXISTS`). It changes only the catalog; no row is read or
  rewritten.

Downgrade is lossy for one kind: Postgres cannot remove an enum value, so the type is built again
without it. The attribution exports are deleted first, with the worker bypass so every project's
rows are reached. The object-store files of the deleted attribution exports are left in place:
expiry works from the rows, so they stay under the project's export prefix until the project is
deleted (which purges the prefix) or they are removed by hand. The column's default cannot be
cast to the new type, so it is dropped and set again around the type change. The lock wait on
`exports` is capped at 5 seconds; if it times out, run the downgrade again.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0803"
down_revision: str | None = "0802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE export_kind ADD VALUE IF NOT EXISTS 'attribution'")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _delete_attribution_exports()
    op.execute("ALTER TYPE export_kind RENAME TO export_kind_0803")
    op.execute("CREATE TYPE export_kind AS ENUM ('traces')")
    op.execute("ALTER TABLE exports ALTER COLUMN kind DROP DEFAULT")
    op.execute(
        "ALTER TABLE exports ALTER COLUMN kind TYPE export_kind USING kind::text::export_kind"
    )
    op.execute("ALTER TABLE exports ALTER COLUMN kind SET DEFAULT 'traces'")
    op.execute("DROP TYPE export_kind_0803")


def _delete_attribution_exports() -> None:
    """Delete every attribution export, seeing every project's rows, then restore the flag.

    No `try/finally`: after an error the transaction is aborted and rolls back, which discards the
    transaction-local setting by itself (as in 0505).
    """
    bind = op.get_bind()
    previous = bind.execute(text("SELECT current_setting('app.bypass_rls', true)")).scalar()
    bind.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    bind.execute(text("DELETE FROM exports WHERE kind = 'attribution'"))
    bind.execute(
        text("SELECT set_config('app.bypass_rls', :previous, true)"),
        {"previous": previous or ""},
    )
