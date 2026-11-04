"""Registered bounded hourly scheduling entry point."""

from typing import Any

from app.jobs.context import TaskContext
from app.rollups.recent_jobs import run_recent


async def run_rollup_hourly(context: TaskContext, _: dict[str, Any]) -> None:
    await run_recent(context)
