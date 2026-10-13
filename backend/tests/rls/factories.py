"""Per-table row factories for the row-level-security tests.

`FACTORIES` holds one `RowFactory` per table that has row-level security. `create(db, table,
project_id)` inserts that table's minimum row for the project, creating the parent rows it needs
first, and returns the inserted row. Run it with the worker bypass set (`bypass_rls`), as the
fixtures do; `insert` runs only the table's own INSERT, for tests that expect it to be refused.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.rls.factories_evals import EVALS
from tests.rls.factories_gateway import GATEWAY
from tests.rls.factories_telemetry import TELEMETRY
from tests.rls.row_spec import RowFactory

Row = dict[str, Any]

FACTORIES: dict[str, RowFactory] = {
    factory.table: factory for factory in (*TELEMETRY, *GATEWAY, *EVALS)
}


async def create_project(db: AsyncSession) -> uuid.UUID:
    """A fresh organization with one project (neither table is under row-level security)."""
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', :slug)"),
        {"id": org_id, "slug": f"org-{org_id.hex[:12]}"},
    )
    await db.execute(
        text(
            "INSERT INTO projects (id, org_id, name, slug) "
            "VALUES (:id, :org_id, 'Project', 'project')"
        ),
        {"id": project_id, "org_id": org_id},
    )
    return project_id


async def create_parents(db: AsyncSession, table: str, project_id: uuid.UUID) -> dict[str, Any]:
    """Create the parent rows `table` needs for the project; returns the INSERT's parameters."""
    params: dict[str, Any] = {"project_id": project_id}
    for needed in FACTORIES[table].parents:
        row = await create(db, needed.table, project_id)
        params.update({name: row[column] for name, column in needed.params.items()})
    return params


async def insert(db: AsyncSession, table: str, params: dict[str, Any]) -> Row:
    """Run only `table`'s own INSERT with these parameters (from `create_parents`)."""
    result = await db.execute(text(FACTORIES[table].sql), params)
    return dict(result.mappings().one())


async def create(db: AsyncSession, table: str, project_id: uuid.UUID) -> Row:
    """Insert `table`'s minimum row for the project, parents first; returns the row."""
    return await insert(db, table, await create_parents(db, table, project_id))
