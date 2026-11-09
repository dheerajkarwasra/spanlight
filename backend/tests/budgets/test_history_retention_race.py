"""Retention coverage marking must follow project-before-trace lock order."""

import asyncio
from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db.rls import bind_project
from app.end_users.erasure_job import erase_end_user
from tests.budgets.support import BudgetWorld, ingest_spend
from tests.jobs.retention_support import apply_retention


async def _wait_for_lock(
    world: BudgetWorld, blocker_pids: list[int], *, exclude_pid: int = 0
) -> int:
    async with asyncio.timeout(5):
        async with world.sessions() as observer:
            while True:
                await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                pid = await observer.scalar(
                    text(
                        "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock' AND pid != :exclude_pid "
                        "AND pg_blocking_pids(pid) && CAST(:blocker_pids AS integer[]) "
                        "LIMIT 1"
                    ),
                    {"blocker_pids": blocker_pids, "exclude_pid": exclude_pid},
                )
                if pid is not None:
                    return pid
                await asyncio.sleep(0.01)


@pytest.mark.parametrize("observer_failure", [None, "history", "project"])
@pytest.mark.parametrize("erase", [False, True])
async def test_history_deletion_and_project_deletion_do_not_deadlock(
    budget_world: BudgetWorld,
    erase: bool,
    observer_failure: str | None,
) -> None:
    world = budget_world
    await ingest_spend(world, user="subject")
    project = await world.workspace.owner.get(world.project_url)
    expectation = (
        pytest.raises(RuntimeError, match="controlled observer timeout")
        if observer_failure
        else nullcontext()
    )
    tasks: list[asyncio.Task] = []
    try:
        with expectation:
            await _history_deletion_race(
                world, erase, project.json()["slug"], observer_failure, tasks
            )
        assert all(task.done() for task in tasks)
        async with world.sessions() as probe:
            await bind_project(probe, world.workspace.project_id)
            await probe.execute(
                text("SELECT id FROM projects WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": world.workspace.project_id},
            )
    finally:
        # Keep a broken cleanup regression from leaking work into the next test.
        await _cancel_tasks(tasks)


async def _history_deletion_race(
    world: BudgetWorld,
    erase: bool,
    slug: str,
    observer_failure: str | None,
    tasks: list[asyncio.Task],
) -> None:
    async with world.sessions() as locker:
        locker_pid = await locker.scalar(text("SELECT pg_backend_pid()"))
        assert locker_pid is not None
        await bind_project(locker, world.workspace.project_id)
        await locker.execute(
            text("SELECT trace_id FROM traces WHERE project_id = :id FOR UPDATE"),
            {"id": world.workspace.project_id},
        )
        retention = asyncio.create_task(
            erase_end_user(
                world.sessions,
                project_id=world.workspace.project_id,
                external_user_id="subject",
                counts={},
            )
            if erase
            else apply_retention(
                world.sessions,
                now=world.clock.wall() + timedelta(days=32),
            )
        )
        tasks.append(retention)
        deletion = None
        try:
            history_pid = await _wait_for_lock(world, [locker_pid])
            if observer_failure == "history":
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await _wait_for_lock(world, [-1])
                raise RuntimeError("controlled observer timeout")
            deletion = asyncio.create_task(
                world.workspace.owner.delete(
                    world.project_url,
                    json={"confirm": slug, "password": "correct horse battery"},
                )
            )
            tasks.append(deletion)
            # Depending on lock order the deletion waits on project or trace. Both establish
            # the real overlap; releasing the trace exposes an inverted order deterministically.
            await _wait_for_lock(world, [locker_pid, history_pid], exclude_pid=history_pid)
            if observer_failure == "project":
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await _wait_for_lock(world, [-1])
                raise RuntimeError("controlled observer timeout")
            await locker.rollback()
            assert deletion is not None
            _, response = await asyncio.wait_for(asyncio.gather(retention, deletion), timeout=10)
            assert response.status_code == 204, response.text
        finally:
            try:
                await locker.rollback()
            finally:
                await _cancel_tasks(tasks)


async def _cancel_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
