"""Indexes on row-level-security tables must be usable by the app role.

Postgres applies a table's policies before the query's own conditions. A query condition may move
below the policy, into an index scan, only when its operator's function is LEAKPROOF; otherwise
the planner keeps it as a filter on rows the policy has already let through. The app connects as a
NOSUPERUSER NOBYPASSRLS role, so an index whose operators are all leaky (GIN `array_ops`,
`jsonb_ops`, `jsonb_path_ops`: `@>`, `&&`, `?`) is never read by the app, yet still costs a write
on every insert. A superuser bypasses the policies and does use such an index, which is why a plan
checked as superuser looks fine.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project

# Access methods whose every index is only as good as its operator family. B-tree indexes are left
# out: their leading `project_id` column is always usable, and some exist for uniqueness.
_NON_BTREE_METHODS = ("gin", "gist", "spgist", "brin", "hash")

# One row per non-B-tree index on an RLS table whose operator family has no leakproof operator.
_UNUSABLE_INDEXES = text(
    """
    SELECT t.relname AS table_name, i.relname AS index_name
    FROM pg_index AS x
    JOIN pg_class AS t ON t.oid = x.indrelid
    JOIN pg_class AS i ON i.oid = x.indexrelid
    JOIN pg_am AS am ON am.oid = i.relam
    CROSS JOIN LATERAL unnest(x.indclass::oid[]) AS k(opclass)
    JOIN pg_opclass AS oc ON oc.oid = k.opclass
    JOIN pg_amop AS ao ON ao.amopfamily = oc.opcfamily
    JOIN pg_operator AS o ON o.oid = ao.amopopr
    JOIN pg_proc AS p ON p.oid = o.oprcode
    WHERE t.relnamespace = current_schema()::regnamespace
      AND t.relrowsecurity
      AND am.amname = ANY (:methods)
    GROUP BY t.relname, i.relname, k.opclass
    HAVING NOT bool_or(p.proleakproof)
    ORDER BY 1, 2
    """
)


async def _plan(db: AsyncSession, sql: str) -> str:
    return "\n".join(await db.scalars(text(f"EXPLAIN (COSTS OFF) {sql}")))


async def test_a_leaky_operator_never_reaches_an_index_under_rls(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The premise of the inventory below, checked on the running Postgres."""
    project_id = uuid.uuid4()
    async with session_factory() as db:
        # The app role owns the tables, so it may add an index for this transaction only.
        await db.execute(text("CREATE INDEX probe_traces_tags_idx ON traces USING gin (tags)"))
        await bind_project(db, project_id)
        await db.execute(text("SET LOCAL enable_seqscan = off"))

        leaky = await _plan(db, "SELECT trace_id FROM traces WHERE tags @> ARRAY['probe']")
        overlap = await _plan(db, "SELECT trace_id FROM traces WHERE tags && ARRAY['probe']")
        leakproof = await _plan(
            db, f"SELECT trace_id FROM traces WHERE project_id = '{project_id}'::uuid"
        )
        await db.rollback()

    assert "probe_traces_tags_idx" not in leaky, leaky
    assert "probe_traces_tags_idx" not in overlap, overlap
    # Control: a leakproof condition (`uuid_eq`) does become an index condition.
    assert "Index Cond" in leakproof, leakproof


async def test_rls_tables_have_no_index_the_app_role_cannot_use(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        rows = (await db.execute(_UNUSABLE_INDEXES, {"methods": list(_NON_BTREE_METHODS)})).all()
    assert [tuple(row) for row in rows] == []


async def test_dataset_items_source_trace_index_is_used_by_the_app_role(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`texteq` is leakproof, so the source-trace lookup reaches its index under RLS."""
    async with session_factory() as db:
        await bind_project(db, uuid.uuid4())
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await _plan(
            db,
            "SELECT dataset_id FROM dataset_items "
            "WHERE source_trace_id = ANY (ARRAY['trace-a', 'trace-b'])",
        )
        await db.rollback()

    assert "dataset_items_source_trace_idx" in plan, plan
