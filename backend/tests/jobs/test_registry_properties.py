"""Bounded registry/submitted-kind combinations preserve unknown rows on real PostgreSQL."""

import asyncio
from typing import Any

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text

from app.config import Settings
from app.db.models import JobStatus
from app.db.session import create_engine, create_session_factory
from app.jobs.context import TaskContext
from app.jobs.worker import Worker
from tests.jobs.registry_support import seed, snapshot


@given(
    registered=st.sets(st.sampled_from(["alpha", "beta", "gamma"])),
    submitted=st.lists(st.sampled_from(["alpha", "beta", "gamma"]), min_size=1, max_size=5),
)
@hypothesis_settings(max_examples=12, derandomize=True, deadline=None)
def test_registry_and_submitted_kind_combinations(
    app_database_url: str, registered: set[str], submitted: list[str]
) -> None:
    async def exercise() -> None:
        engine = create_engine(app_database_url, pool_size=2, max_overflow=0)
        factory = create_session_factory(engine)
        effects: list[int] = []

        async def handler(context: TaskContext, _: dict[str, Any]) -> None:
            effects.append(context.job.id)

        worker = Worker(
            factory,
            Settings(database_url=app_database_url, secret_key="test", _env_file=None),
            handlers=dict.fromkeys(registered, handler),
        )
        try:
            ids = [
                await seed(factory, kind, {"submitted": index})
                for index, kind in enumerate(submitted)
            ]
            before = [await snapshot(factory, job_id) for job_id in ids]
            for _ in range(len(ids) + 2):
                await worker.run_once()
            for job_id, kind, original in zip(ids, submitted, before, strict=True):
                final = await snapshot(factory, job_id)
                if kind in registered:
                    assert effects.count(job_id) == 1
                    assert (final["status"], final["attempts"], final["fence"]) == (
                        JobStatus.DONE,
                        1,
                        1,
                    )
                else:
                    assert job_id not in effects
                    assert final == original
        finally:
            async with factory() as db:
                await db.execute(text("DELETE FROM jobs"))
                await db.commit()
            await engine.dispose()

    asyncio.run(exercise())
