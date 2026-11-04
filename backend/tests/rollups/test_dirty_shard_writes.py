"""RE19: independent shards do not wait for the same project's open transaction."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.rls import bind_project
from app.rollups import dirty
from tests.attribution.support import SessionFactory
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_rows


async def test_different_shards_mark_without_waiting_for_commit(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # raising=False lets this regression reach the old SQL before the switch exists.
    monkeypatch.setattr(dirty, "WRITE_SHARDS", True, raising=False)
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await db.commit()
    async with session_factory() as first, session_factory() as second:
        await bind_project(first, project)
        monkeypatch.setattr(random, "randrange", lambda _: 0)
        await dirty.mark_dirty_hours(first, project, [hour])
        await bind_project(second, project)
        await second.execute(text("SET LOCAL lock_timeout = '200ms'"))
        monkeypatch.setattr(random, "randrange", lambda _: 1)
        await dirty.mark_dirty_hours(second, project, [hour])
        await second.commit()
        await first.commit()
    async with session_factory() as db:
        await bind_project(db, project)
        rows = await dirty_rows(db)
        assert [r.shard for r in rows] == [0, 1]
        assert [r.pending_consumers for r in rows] == [15, 15]
        assert rows[0].generation != rows[1].generation


@pytest.mark.parametrize("sharded", [False, True])
async def test_writer_uses_one_shard_for_sorted_unique_hours_and_retains_age(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch, sharded: bool
) -> None:
    monkeypatch.setattr(dirty, "WRITE_SHARDS", sharded, raising=False)
    choices = iter([3, 7])
    monkeypatch.setattr(random, "randrange", lambda _: next(choices))
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await dirty.mark_dirty_hours(db, project, [hour + timedelta(hours=1), hour, hour])
        rows = await dirty_rows(db)
        assert [r.bucket_hour for r in rows] == [hour, hour + timedelta(hours=1)]
        assert [r.shard for r in rows] == ([3, 3] if sharded else [-1, -1])
        first = rows[0]
        assert await dirty.complete_dirty_consumers(
            db, project, hour, first.generation, dirty.HOURLY, shard=first.shard
        )
        await db.commit()
        await bind_project(db, project)
        monkeypatch.setattr(random, "randrange", lambda _: 3)
        await dirty.mark_dirty_hours(db, project, [hour])
        current = (await dirty_rows(db))[0]
        assert current.generation != first.generation
        assert current.first_marked_at == first.first_marked_at
        assert current.pending_consumers == dirty.ALL
        assert not await dirty.ack_dirty_hour(
            db, project, hour, first.generation, shard=first.shard
        )
