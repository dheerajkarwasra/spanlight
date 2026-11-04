"""Registered hourly jobs must make durable progress past an unavailable first tenant."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text

from app.config import Settings
from tests.jobs.bypass_support import SessionFactory, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import run_recent, state


async def test_locked_first_tenant_does_not_starve_healthy_tenant(
    session_factory: SessionFactory, settings: Settings
) -> None:
    first, second = sorted(await new_projects(session_factory, 2))
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, first, 1, started_at=now)
    await seed_llm_spans(session_factory, second, 2, started_at=now)
    async with session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"aggregate-source-loss:{first}"},
        )
        try:
            async with asyncio.timeout(2):
                await run_recent(session_factory, settings, now)
        except TimeoutError:
            pass
    assert await fetch(
        session_factory,
        "SELECT sum(span_count) AS n FROM span_rollups_hourly WHERE project_id=:p",
        {"p": second},
    ) == [{"n": 2}]
    assert (await state(session_factory, first))["attempt_order"] > 0
    assert (await state(session_factory, second))["attempt_order"] > (
        await state(session_factory, first)
    )["attempt_order"]
