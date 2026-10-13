"""Every table under row-level security keeps one project's rows away from another.

Each case seeds one row of project A (with the worker bypass, as background jobs write) and then
works through the app role, which Postgres holds to the policies. The table list is the factory
registry; `test_every_rls_table_has_a_row_factory` checks it against what the database reports,
so a new table with row-level security and no factory fails here.
"""

import uuid
from dataclasses import dataclass
from typing import Any, cast

import pytest
from sqlalchemy import CursorResult, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from tests.rls.factories import FACTORIES, create, create_parents, create_project, insert

TABLES = sorted(FACTORIES)

Sessions = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class Seeded:
    """Project A's row in `table`, the parameters it was inserted with, and a second project."""

    table: str
    project_a: uuid.UUID
    project_b: uuid.UUID
    params: dict[str, Any]


async def _seed(session_factory: Sessions, table: str) -> Seeded:
    async with session_factory() as db:
        await bypass_rls(db)
        project_a, project_b = await create_project(db), await create_project(db)
        params = await create_parents(db, table, project_a)
        await insert(db, table, params)
        await db.commit()
    return Seeded(table=table, project_a=project_a, project_b=project_b, params=params)


async def _count(db: AsyncSession, table: str) -> int:
    count = await db.scalar(text(f"SELECT count(*) FROM {table}"))
    assert count is not None
    return int(count)


async def _rowcount(db: AsyncSession, statement: str) -> int:
    result = cast(CursorResult[Any], await db.execute(text(statement)))
    return result.rowcount


async def test_every_rls_table_has_a_row_factory(session_factory: Sessions) -> None:
    async with session_factory() as db:
        secured = await db.scalars(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relrowsecurity"
            )
        )
        assert set(secured) == set(FACTORIES)


@pytest.mark.parametrize("table", TABLES)
async def test_an_unbound_session_sees_no_rows(session_factory: Sessions, table: str) -> None:
    await _seed(session_factory, table)
    async with session_factory() as db:
        assert await _count(db, table) == 0


@pytest.mark.parametrize("table", TABLES)
async def test_another_project_cannot_read_update_or_delete_the_row(
    session_factory: Sessions, table: str
) -> None:
    seeded = await _seed(session_factory, table)
    touch = FACTORIES[table].touch
    async with session_factory() as db:
        await bind_project(db, seeded.project_b)
        assert await _count(db, table) == 0
        assert await _rowcount(db, f"UPDATE {table} SET {touch} = {touch}") == 0
        assert await _rowcount(db, f"DELETE FROM {table}") == 0
        await db.commit()
    async with session_factory() as db:
        await bypass_rls(db)
        assert await _count(db, table) == 1  # the row survived the DELETE


@pytest.mark.parametrize("table", TABLES)
async def test_another_project_cannot_insert_a_row_for_this_project(
    session_factory: Sessions, table: str
) -> None:
    seeded = await _seed(session_factory, table)
    async with session_factory() as db:
        await bind_project(db, seeded.project_b)
        # Project A's id, or for a child table A's parent row: the policy refuses the new row.
        with pytest.raises(DBAPIError, match="row-level security"):
            await insert(db, table, seeded.params)


@pytest.mark.parametrize("table", TABLES)
async def test_the_owning_project_and_the_worker_bypass_see_the_row(
    session_factory: Sessions, table: str
) -> None:
    seeded = await _seed(session_factory, table)
    async with session_factory() as db:
        await bind_project(db, seeded.project_a)
        assert await _count(db, table) == 1
    async with session_factory() as db:
        await bypass_rls(db)
        assert await _count(db, table) == 1


async def test_a_dataset_item_cannot_be_added_to_another_projects_dataset(
    session_factory: Sessions,
) -> None:
    # `dataset_items` has no project_id of its own: its policy asks whether the parent dataset is
    # visible, which is a different mechanism from the column check.
    async with session_factory() as db:
        await bypass_rls(db)
        project_a, project_b = await create_project(db), await create_project(db)
        dataset_a = await create(db, "datasets", project_a)
        dataset_b = await create(db, "datasets", project_b)
        await db.commit()
    async with session_factory() as db:
        await bind_project(db, project_b)
        await insert(db, "dataset_items", {"dataset_id": dataset_b["id"]})  # its own: allowed
        with pytest.raises(DBAPIError, match="row-level security"):
            await insert(db, "dataset_items", {"dataset_id": dataset_a["id"]})
