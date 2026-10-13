"""The catalog view of tenant isolation: which tables carry row-level security, and which policies.

Read from `pg_class` and `pg_policies`, so the next migration that adds a project-scoped table
and forgets its policies fails here, before any data test has to notice.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

Sessions = async_sessionmaker[AsyncSession]

# Tables that have a `project_id` column and deliberately no row-level security.
EXCEPTIONS = {
    # Looked up by prefix before the project is known (ADR 0003, "Exception: api_keys").
    "api_keys",
}

# Tables without a `project_id` of their own, isolated through their parent's policy.
CHILD_TABLES = {
    "prompt_versions",
    "prompt_labels",
    "dataset_items",
    "annotation_queue_items",
    "evaluator_runs",
    "experiment_runs",
    "experiment_items",
}

# Extra policies beyond the two every secured table has.
EXTRA_POLICIES = {"gateway_keys": {"gateway_keys_prefix_lookup": "SELECT"}}


async def _names(db: AsyncSession, sql: str) -> set[str]:
    return set(await db.scalars(text(sql)))


async def _project_scoped(db: AsyncSession) -> set[str]:
    return await _names(
        db,
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
        "AND a.attname = 'project_id' AND NOT a.attisdropped",
    )


async def _secured(db: AsyncSession) -> set[str]:
    """Tables with row-level security both enabled and forced on the table owner."""
    return await _names(
        db,
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
        "AND c.relrowsecurity AND c.relforcerowsecurity",
    )


async def test_every_project_scoped_table_has_forced_rls_except_the_documented_ones(
    session_factory: Sessions,
) -> None:
    async with session_factory() as db:
        project_scoped = await _project_scoped(db)
        secured = await _secured(db)
    assert project_scoped - secured == EXCEPTIONS
    assert project_scoped >= EXCEPTIONS  # an exception that lost its column is stale


async def test_the_child_tables_have_forced_rls(session_factory: Sessions) -> None:
    async with session_factory() as db:
        project_scoped = await _project_scoped(db)
        secured = await _secured(db)
    assert secured >= CHILD_TABLES
    assert not CHILD_TABLES & project_scoped
    # Nothing else is secured: the secured set is exactly what this file names.
    assert secured == (project_scoped - EXCEPTIONS) | CHILD_TABLES


async def test_every_secured_table_has_exactly_the_isolation_and_bypass_policies(
    session_factory: Sessions,
) -> None:
    async with session_factory() as db:
        secured = await _secured(db)
        rows = (
            await db.execute(
                text(
                    "SELECT tablename, policyname, cmd, permissive, roles::text[] AS roles "
                    "FROM pg_policies WHERE schemaname = 'public'"
                )
            )
        ).all()
    policies: dict[str, dict[str, str]] = {}
    for row in rows:
        assert row.permissive == "PERMISSIVE", row
        assert list(row.roles) == ["public"], row
        policies.setdefault(row.tablename, {})[row.policyname] = row.cmd
    assert set(policies) == secured  # no policy sits on a table without RLS turned on
    for table in sorted(secured):
        expected = {f"{table}_project_isolation": "ALL", f"{table}_worker_bypass": "ALL"}
        expected |= EXTRA_POLICIES.get(table, {})
        assert policies[table] == expected, table


@pytest.mark.parametrize(
    ("suffix", "setting"),
    [("project_isolation", "app.project_id"), ("worker_bypass", "app.bypass_rls")],
)
async def test_each_policy_reads_its_own_setting_for_reads_and_writes(
    session_factory: Sessions, suffix: str, setting: str
) -> None:
    async with session_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT tablename, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'public' AND policyname = tablename || '_' || :suffix"
                ),
                {"suffix": suffix},
            )
        ).all()
    assert rows
    for row in rows:
        # The child tables reach `app.project_id` through their parent's policy.
        if row.tablename not in CHILD_TABLES or suffix == "worker_bypass":
            assert f"'{setting}'" in row.qual, row
        # The same expression guards reads and writes, so no row can be written that is not read.
        assert row.with_check == row.qual, row
    assert all("app.bypass_rls" not in row.qual for row in rows if suffix == "project_isolation")


async def test_the_gateway_key_prefix_policy_only_reads(session_factory: Sessions) -> None:
    async with session_factory() as db:
        row = (
            await db.execute(
                text(
                    "SELECT cmd, qual, with_check FROM pg_policies "
                    "WHERE policyname = 'gateway_keys_prefix_lookup'"
                )
            )
        ).one()
    assert row.cmd == "SELECT"
    assert row.with_check is None
    assert "'app.gateway_key_prefix'" in row.qual
