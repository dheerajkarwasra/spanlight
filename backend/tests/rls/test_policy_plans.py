"""The row-level-security policies plan as InitPlans, not as a function call per row.

Migration 0017 wrapped every `current_setting()` in `(SELECT …)` so Postgres reads the setting
once per statement; the later tables were written that way from the start (ADR 0003,
amendment). A policy written the old way shows `current_setting(...)` in the scan's filter.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project
from tests.rls.factories import FACTORIES


@pytest.mark.parametrize("table", sorted(FACTORIES))
async def test_rls_policies_plan_as_initplans(
    session_factory: async_sessionmaker[AsyncSession], table: str
) -> None:
    async with session_factory() as db:
        await bind_project(db, uuid.uuid4())
        plan = "\n".join(await db.scalars(text(f"EXPLAIN SELECT * FROM {table}")))
    assert "InitPlan" in plan, plan
    assert "current_setting" not in plan, plan
