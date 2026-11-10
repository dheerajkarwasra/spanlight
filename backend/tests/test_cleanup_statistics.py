"""No test inherits planner statistics from the rows of an earlier test."""

import os
from collections.abc import Awaitable, Callable

import psycopg
import pytest

from tests.attribution.support import SessionFactory
from tests.ingest.faults import admin_libpq_url
from tests.rls.factories import create_project

_STATISTICS = """
    SELECT c.reltuples, (SELECT count(*) FROM pg_statistic s WHERE s.starelid = c.oid)
    FROM pg_class c WHERE c.oid = 'projects'::regclass
"""


async def test_cleanup_forgets_statistics(
    session_factory: SessionFactory,
    _empty_tables: Callable[[], Awaitable[None]],  # noqa: PT019 - the cleanup itself is called.
) -> None:
    async with session_factory() as db:
        await create_project(db)
        await db.commit()
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        await admin.execute("ANALYZE projects")
        row = await (await admin.execute(_STATISTICS)).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] > 0
        await _empty_tables()
        # -1 is "never analyzed": the planner sizes the table from its pages, and no column
        # statistics name the ids and times of rows that are gone.
        assert await (await admin.execute(_STATISTICS)).fetchone() == (-1, 0)


async def test_cleanup_gives_back_the_pages_of_a_table_that_grew(
    _empty_tables: Callable[[], Awaitable[None]],  # noqa: PT019 - the cleanup itself is called.
) -> None:
    """Nothing vacuums the test server, so dead rows would otherwise be re-read by every cleanup."""
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        await admin.execute(
            "INSERT INTO jobs (kind) SELECT repeat('x', 1000) FROM generate_series(1, 3000)"
        )
        size = "SELECT pg_relation_size('jobs')"
        assert await (await admin.execute(size)).fetchone() > (1024 * 1024,)
        await _empty_tables()
        assert await (await admin.execute(size)).fetchone() == (0,)


@pytest.mark.skipif("TEST_DATABASE_URL" in os.environ, reason="the server is not the harness's")
async def test_nothing_rewrites_statistics_in_the_middle_of_a_test() -> None:
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        assert await (await admin.execute("SHOW autovacuum")).fetchone() == ("off",)
