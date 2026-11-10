"""The per-test cleanup leaves no planner statistics that call a table empty."""

from collections.abc import Awaitable, Callable

import psycopg

from tests.attribution.support import SessionFactory
from tests.ingest.faults import admin_libpq_url
from tests.rls.factories import create_project


async def test_cleanup_forgets_empty_statistics(
    session_factory: SessionFactory,
    _empty_tables: Callable[[], Awaitable[None]],  # noqa: PT019 - the cleanup itself is called.
) -> None:
    async with session_factory() as db:
        await create_project(db)
        await db.commit()
    await _empty_tables()
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        # What autovacuum does between two tests: the rows are dead, their pages remain.
        await admin.execute("ANALYZE projects")
        stats = "SELECT reltuples, relpages > 0 FROM pg_class WHERE oid = 'projects'::regclass"
        assert await (await admin.execute(stats)).fetchone() == (0, True)
        await _empty_tables()
        # -1 is "never analyzed": the planner sizes the table from its pages, not as one row.
        assert await (await admin.execute(stats)).fetchone() == (-1, True)
