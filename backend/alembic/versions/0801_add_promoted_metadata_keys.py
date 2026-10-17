"""Add `projects.promoted_metadata_keys`, the metadata keys the daily attribution rollup keeps.

Revision ID: 0801
Revises: 0800
Create Date: 2026-10-16

A project promotes up to five metadata keys; the attribution rollup job keeps one row per value
of each promoted key and day, so a window longer than a day can be grouped by it.

Choices worth knowing about:

* `text[] NOT NULL DEFAULT '{}'`: no key is promoted until a project admin says so. A constant
  default, so the column add rewrites nothing.
* `projects_promoted_keys_count_check` caps the list at five keys.
* `projects_promoted_keys_pattern_check` holds every key to the metadata key pattern
  (`[a-z0-9_.-]{1,64}`), as a backstop for the API, which validates each key before it is
  stored. A CHECK cannot run a subquery over the array, so it matches the comma-joined list
  against the pattern repeated. Joined alone, that would accept an element holding a comma (it
  reads as two keys), a NULL element (`array_to_string` skips it) and a lone empty string (it
  joins to the empty list's ''). So NULL elements join as '' (the third argument), which the
  pattern rejects, an empty list is allowed only through `cardinality = 0`, and the number of
  separators must be one less than the number of elements, so no element can hold a comma. The
  list must also be one-dimensional (`array_ndims`, NULL for the empty list): `array_to_string`
  flattens `{{a},{b}}` to a valid `a,b`.
* `projects` is small and every existing row holds the default, so the column and its CHECKs
  are added in one statement, in the migration's transaction.

Downgrade drops the column, and with it the CHECKs and every project's promoted keys.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0801"
down_revision: str | None = "0800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
KEY = "[a-z0-9_.-]{1,64}"
# The keys joined by commas, a NULL element as '' so the pattern rejects it.
JOINED = "array_to_string(promoted_metadata_keys, ',', '')"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        f"""
        ALTER TABLE projects
            ADD COLUMN promoted_metadata_keys text[] NOT NULL DEFAULT '{{}}',
            ADD CONSTRAINT projects_promoted_keys_count_check
                CHECK (cardinality(promoted_metadata_keys) <= 5),
            ADD CONSTRAINT projects_promoted_keys_pattern_check CHECK (
                coalesce(array_ndims(promoted_metadata_keys), 1) = 1
                AND (
                    cardinality(promoted_metadata_keys) = 0 OR (
                        {JOINED} ~ '^{KEY}(,{KEY})*$'
                        AND cardinality(promoted_metadata_keys)
                            = 1 + length({JOINED}) - length(replace({JOINED}, ',', ''))
                    )
                )
            )
        """
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS promoted_metadata_keys")
