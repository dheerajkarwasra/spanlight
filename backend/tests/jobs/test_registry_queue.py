"""Direct callers can restrict kinds or explicitly request unrestricted queue behavior."""

from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.models import JobStatus
from app.ee import hooks as ee
from app.jobs import queue
from app.jobs.context import TaskContext
from app.jobs.tasks import TASKS
from app.jobs.worker import Worker
from tests.jobs.registry_support import SessionFactory, seed, snapshot


async def test_queue_filter_precedes_claim_and_exhaustion(session_factory: SessionFactory) -> None:
    unknown_id = await seed(session_factory, "unknown")
    exhausted_id = await seed(session_factory, "unknown", attempts=3)
    before = (
        await snapshot(session_factory, unknown_id),
        await snapshot(session_factory, exhausted_id),
    )
    supported_id = await seed(session_factory, "known")
    async with session_factory() as db:
        job = await queue.claim_next(db, supported_kinds={"known"})
        assert job is not None and job.id == supported_id
        await queue.complete(db, job)
        assert await queue.claim_next(db, supported_kinds={"known"}) is None
    assert (
        await snapshot(session_factory, unknown_id),
        await snapshot(session_factory, exhausted_id),
    ) == before
    async with session_factory() as db:
        job = await queue.claim_next(db, supported_kinds=None)
        assert job is not None and job.id == unknown_id
    assert (await snapshot(session_factory, exhausted_id))["status"] is JobStatus.FAILED


async def test_empty_supported_kinds_claims_and_expires_nothing(
    session_factory: SessionFactory,
) -> None:
    job_id = await seed(session_factory, "known")
    exhausted_id = await seed(session_factory, "known", attempts=3)
    before = await snapshot(session_factory, job_id), await snapshot(session_factory, exhausted_id)
    async with session_factory() as db:
        assert await queue.claim_next(db, supported_kinds=()) is None
    assert (
        await snapshot(session_factory, job_id),
        await snapshot(session_factory, exhausted_id),
    ) == before


@pytest.mark.parametrize("handler_version", [1, 2])
@pytest.mark.parametrize("payload", [{"value": "old"}, {"value": "new", "optional": "added"}])
async def test_same_kind_payloads_require_both_handler_versions(
    session_factory: SessionFactory,
    settings: Settings,
    handler_version: int,
    payload: dict[str, Any],
) -> None:
    effects: list[str] = []

    async def old_handler(_: TaskContext, body: dict[str, Any]) -> None:
        effects.append(body["value"])

    async def new_handler(_: TaskContext, body: dict[str, Any]) -> None:
        effects.append(body["value"])
        assert body.get("optional", "added") == "added"

    job_id = await seed(session_factory, "compatible", payload)
    worker = Worker(
        session_factory,
        settings,
        handlers={"compatible": old_handler if handler_version == 1 else new_handler},
    )
    assert await worker.run_once()
    assert effects == [payload["value"]]
    assert (await snapshot(session_factory, job_id))["status"] is JobStatus.DONE


async def test_exact_registry_keys_include_enterprise_registrations(
    session_factory: SessionFactory, settings: Settings
) -> None:
    # Enterprise hooks are installation-owned; this test does not activate a license or provider.
    keys = {"ee_custom_contract", *ee.tasks(), *TASKS}
    observed: list[str] = []

    async def handler(context: TaskContext, _: dict[str, Any]) -> None:
        observed.append(context.job.kind)

    worker = Worker(session_factory, settings, handlers=dict.fromkeys(keys, handler))
    for kind in sorted(keys):
        await seed(session_factory, kind)
    while await worker.run_once():
        # Some actual registry keys occupy a long slot.
        for slot in (worker._long_job, worker._maintenance_job):
            if slot is not None:
                await slot
    assert sorted(observed) == sorted(keys)


async def test_registry_tests_use_disposable_database_and_unprivileged_role(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        identity = (
            await db.execute(
                text(
                    "SELECT current_database(), current_user, rolsuper, rolbypassrls, "
                    "current_setting('server_version_num')::int "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    print("REGISTRY_DATABASE_IDENTITY", tuple(identity))
    assert identity[0].startswith("spanlight_test_")
    assert identity[1:4] == ("spanlight_app_test", False, False)
    assert 170000 <= identity[4] < 180000
