"""Durable marker generations, rollback and unprivileged tenant isolation."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.db.rls import bind_project, bypass_rls
from app.rollups.dirty import (
    ALL,
    HOURLY,
    ack_dirty_hour,
    complete_dirty_consumers,
    mark_dirty_hours,
)
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace
from tests.rollups.dirty_support import dirty_row, dirty_rows

HOUR = datetime(2026, 10, 20, 10, tzinfo=UTC)


async def test_mark_normalizes_deduplicates_preserves_age_and_replaces_token(
    browser_factory: Any, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    workspace = await create_workspace(await browser_factory("dirty-normalize@example.com"))
    project = uuid.UUID(workspace.project_id)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(
            db,
            project,
            [HOUR, HOUR + timedelta(minutes=59), HOUR.astimezone(timezone(timedelta(hours=5)))],
        )
        original = await dirty_row(db)
        assert original.bucket_hour == HOUR
        assert original.pending_consumers == ALL
        assert original.generation > 0
        assert await complete_dirty_consumers(
            db, project, HOUR, original.generation, HOURLY, shard=original.shard
        )
        await mark_dirty_hours(db, project, [HOUR])
        changed = await dirty_row(db)
        assert changed.first_marked_at == original.first_marked_at
        assert changed.generation != original.generation
        assert changed.pending_consumers == ALL


@pytest.mark.parametrize("sharded", [False, True])
async def test_stale_generation_cannot_complete_or_ack_after_delete_reinsert(
    browser_factory: Any,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    sharded: bool,
) -> None:
    monkeypatch.setattr("app.rollups.dirty.WRITE_SHARDS", sharded)
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    workspace = await create_workspace(await browser_factory("dirty-aba@example.com"))
    project = uuid.UUID(workspace.project_id)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [HOUR])
        observed = await dirty_row(db)
        original, shard = observed.generation, observed.shard
        assert not await ack_dirty_hour(db, project, HOUR, original, shard=shard)
        assert await complete_dirty_consumers(db, project, HOUR, original, ALL, shard=shard)
        assert await ack_dirty_hour(db, project, HOUR, original, shard=shard)
        await mark_dirty_hours(db, project, [HOUR])
        replacement = (await dirty_row(db)).generation
        assert replacement != original
        assert not await complete_dirty_consumers(db, project, HOUR, original, ALL, shard=shard)
        assert not await ack_dirty_hour(db, project, HOUR, original, shard=shard)
        assert (await dirty_row(db)).pending_consumers == ALL
        # An empty replacement isolates ACK's own generation predicate from its mask check.
        assert await complete_dirty_consumers(db, project, HOUR, replacement, ALL, shard=shard)
        assert not await ack_dirty_hour(db, project, HOUR, original, shard=shard)
        assert await ack_dirty_hour(db, project, HOUR, replacement, shard=shard)


@pytest.mark.parametrize("mask", [0, -1, 16, 31])
async def test_completion_rejects_invalid_masks(
    browser_factory: Any, session_factory: SessionFactory, mask: int
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-mask@example.com"))
    project = uuid.UUID(workspace.project_id)
    async with session_factory() as db:
        await bind_project(db, project)
        with pytest.raises(ValueError, match="nonzero subset"):
            await complete_dirty_consumers(db, project, HOUR, 1, mask)


async def test_last_bit_and_ack_roll_back_atomically(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-rollback@example.com"))
    project = uuid.UUID(workspace.project_id)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [HOUR])
        observed = await dirty_row(db)
        generation, shard = observed.generation, observed.shard
        assert await complete_dirty_consumers(
            db, project, HOUR, generation, ALL ^ HOURLY, shard=shard
        )
        await db.commit()
        await bind_project(db, project)
        assert await complete_dirty_consumers(db, project, HOUR, generation, HOURLY, shard=shard)
        assert await ack_dirty_hour(db, project, HOUR, generation, shard=shard)
        await db.rollback()
        await bind_project(db, project)
        row = await dirty_row(db)
        assert row.generation == generation
        assert row.pending_consumers == HOURLY


async def test_marker_reads_and_completion_are_tenant_scoped(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    first = await create_workspace(await browser_factory("dirty-tenant-a@example.com"))
    second = await create_workspace(await browser_factory("dirty-tenant-b@example.com"))
    project_a, project_b = uuid.UUID(first.project_id), uuid.UUID(second.project_id)
    async with session_factory() as db:
        await bind_project(db, project_a)
        await mark_dirty_hours(db, project_a, [HOUR])
        observed = await dirty_row(db)
        generation, shard = observed.generation, observed.shard
        await db.commit()
        await bind_project(db, project_b)
        assert await dirty_rows(db) == []
        assert not await complete_dirty_consumers(db, project_a, HOUR, generation, ALL, shard=shard)
        assert not await ack_dirty_hour(db, project_a, HOUR, generation, shard=shard)


async def test_worker_completion_still_checks_requested_project(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-worker-scope@example.com"))
    project = uuid.UUID(workspace.project_id)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [HOUR])
        observed = await dirty_row(db)
        generation, shard = observed.generation, observed.shard
        await bypass_rls(db)
        assert not await complete_dirty_consumers(
            db, uuid.uuid4(), HOUR, generation, ALL, shard=shard
        )
        assert (await dirty_row(db)).pending_consumers == ALL
