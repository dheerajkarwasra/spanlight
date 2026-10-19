"""Index a bounded text preview of span input and output for content search.

Revision ID: 1006
Revises: 1003
Create Date: 2026-10-19

The trace list's `q` also matches what was sent to and returned by a span, not only the trace
name. Searching the full payloads (up to 32 KB a side) with `ILIKE '%…%'` reads every span of
the window, so the search runs against a preview instead: `spanlight_search_text(input, output)`,
the last 2,000 characters of the input (the latest message of a chat) and the first 2,000 of the
output, as JSON text. A GIN index with `pg_trgm`'s `gin_trgm_ops` over that function answers
`ILIKE` with a substring pattern of three characters or more.

Row-level security: `spans` forces its policy, and Postgres only uses a condition as an index
condition ahead of the policy when every function in it that reads a column is LEAKPROOF (it
reveals nothing about its arguments except through its result). Without that the index is never
used and a search scans the whole window (21-23 s on a 4.2 M-span project). Both functions are
leakproof in fact: `spanlight_search_text` only slices and concatenates text and cannot fail on
stored data, and `texticlike` (the function behind `ILIKE`) can only fail on a malformed pattern,
which is the searcher's own input, never a stored row. Marking them is not a bypass: the policy
still filters every row. Only a superuser can mark a function LEAKPROOF, so the marks are applied
when the migration runs as one (Compose and Railway do) and skipped otherwise: search is then
correct but slow. `texticlike` is a catalog function, so `pg_dump` does not keep its mark;
`spanlight ensure-app-role`, which runs after every restore and on every Compose deploy, sets it
again (`app.db.roles`).

`pg_trgm` ships with Postgres (contrib) and is a trusted extension, so a role with `CREATE` on
the database can create it. The index is built `CONCURRENTLY` in `autocommit_block()`, so ingest
keeps writing while it builds; an INVALID index left by an interrupted run is dropped and built
again, as in 1004. On a 4.2 M-span table the build took about 24 minutes and 1.35 GB.

Downgrade drops the index and the function and unmarks `texticlike`; it keeps the extension,
which other objects may use.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1006"
down_revision: str | None = "1003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "spans_content_trgm_idx"
# PL/pgSQL, not SQL: a SQL function would be inlined into the query and no longer match the index.
# Names are schema-qualified so the result does not depend on the caller's search_path.
CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION spanlight_search_text(input jsonb, output jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
BEGIN
    RETURN pg_catalog.right(coalesce(input::text, ''), 2000) || ' '
        || pg_catalog.left(coalesce(output::text, ''), 2000);
END
$$
"""
MARK_LEAKPROOF = """
DO $$
BEGIN
    IF current_setting('is_superuser') = 'on' THEN
        ALTER FUNCTION spanlight_search_text(jsonb, jsonb) LEAKPROOF;
        ALTER FUNCTION pg_catalog.texticlike(text, text) LEAKPROOF;
    ELSE
        RAISE WARNING 'content search: not a superuser, functions not marked LEAKPROOF; '
            'the search index is not used under row-level security';
    END IF;
END
$$
"""
UNMARK_LEAKPROOF = """
DO $$
BEGIN
    IF current_setting('is_superuser') = 'on' THEN
        ALTER FUNCTION pg_catalog.texticlike(text, text) NOT LEAKPROOF;
    END IF;
END
$$
"""


def _index_is_valid(name: str) -> bool | None:
    """True if the index exists and is valid, False if it exists but is INVALID, None if absent.

    Offline (`--sql`) there is no database to ask, so the answer is None and the statements below
    are the unconditional ones.
    """
    context = op.get_context()
    if context.as_sql:
        return None
    row = context.bind.execute(  # type: ignore[union-attr]  # bind is set when not as_sql
        sa.text(
            "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND c.relnamespace = current_schema()::regnamespace"
        ),
        {"name": name},
    ).first()
    return None if row is None else bool(row[0])


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(CREATE_FUNCTION)
    op.execute(MARK_LEAKPROOF)
    with op.get_context().autocommit_block():
        state = _index_is_valid(INDEX)
        if state is True:
            return
        if state is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX} ON spans "
            "USING gin (spanlight_search_text(input, output) gin_trgm_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    op.execute("DROP FUNCTION IF EXISTS spanlight_search_text(jsonb, jsonb)")
    op.execute(UNMARK_LEAKPROOF)
