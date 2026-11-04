"""A mark waiting on final acknowledgment must recreate all obligations with a fresh token."""

import asyncio
import random
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from app.db.rls import bind_project
from app.rollups import dirty
from app.rollups.dirty import ALL, ack_dirty_hour, complete_dirty_consumers, mark_dirty_hours
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace
from tests.rollups.dirty_support import dirty_rows


@pytest.mark.parametrize("sharded", [False, True])
async def test_mark_waiting_on_ack_recreates_all_obligations(
    browser_factory: Any,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    sharded: bool,
) -> None:
    monkeypatch.setattr(dirty, "WRITE_SHARDS", sharded, raising=False)
    monkeypatch.setattr(random, "randrange", lambda _: 5)
    workspace = await create_workspace(await browser_factory("dirty-ack-race@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        observed = (await dirty_rows(db))[0]
        generation, shard = observed.generation, observed.shard
        await db.commit()

    async def remark() -> None:
        async with session_factory() as writer:
            await bind_project(writer, project)
            await mark_dirty_hours(writer, project, [hour])
            await writer.commit()

    async with session_factory() as consumer:
        await bind_project(consumer, project)
        # Empty complete sources have no materialized contribution to rebuild.
        assert await complete_dirty_consumers(consumer, project, hour, generation, ALL, shard=shard)
        assert await ack_dirty_hour(consumer, project, hour, generation, shard=shard)
        pid = await consumer.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(remark())
        try:
            async with asyncio.timeout(5), session_factory() as observer:
                while not await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname=current_database() AND :pid=ANY(pg_blocking_pids(pid)))"
                    ),
                    {"pid": pid},
                ):
                    await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    await asyncio.sleep(0.01)
            assert not task.done()
            await consumer.commit()
            await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with session_factory() as db:
        await bind_project(db, project)
        rows = await dirty_rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row.generation != generation
        assert row.pending_consumers == ALL
        assert not await complete_dirty_consumers(db, project, hour, generation, ALL, shard=shard)
