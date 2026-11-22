"""Add 'gemini' to PostgreSQL enum provider_kind.

Revision ID: 1049
Revises: 1048
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1049"
down_revision: str | None = "1048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE provider_kind ADD VALUE IF NOT EXISTS 'gemini'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum in-place safely.
    pass
