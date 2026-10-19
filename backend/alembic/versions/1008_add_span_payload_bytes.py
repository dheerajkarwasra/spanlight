"""Add `spans.payload_bytes`.

Revision ID: 1008
Revises: 1007
Create Date: 2026-10-19

The payload cap goes from 32 KB to 1 MiB per input and output. Trace detail, exports and the
gateway backlog then need the stored size of a span's payloads to keep their memory bounded, and
Postgres cannot report a TOASTed value's uncompressed size without decompressing it. Ingestion
writes the size it already measured for the cap check.

Nullable with no default, so adding it changes only the catalog. Spans stored before it keep NULL
and are read as the most the old cap allowed (`LEGACY_PAYLOAD_BYTES`). As in 0900, the add runs
outside the migration's transaction and waits at most 5 seconds for its ACCESS EXCLUSIVE lock
rather than stall every ingest queued behind the wait; `IF NOT EXISTS` makes a rerun safe.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1008"
down_revision: str | None = "1007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE spans ADD COLUMN IF NOT EXISTS payload_bytes integer")
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute("ALTER TABLE spans DROP COLUMN IF EXISTS payload_bytes")
        op.execute("RESET lock_timeout")
