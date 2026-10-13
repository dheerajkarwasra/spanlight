"""Shared setup for the tests of jobs that run with the row-level-security bypass.

Each such job reads and writes across projects, so its tests share one shape: seed rows for
several projects, run the job, then read back per project. Seeding and reading back both use the
worker bypass; `visible_to` reads as the API does (bound to one project) to check that what the
job wrote cannot be seen from another project.

- `new_projects(session_factory, count)`: that many organizations, each with one project.
- `seed_row(session_factory, table, project_id)`: one row of an RLS table from the row factories.
- `seed_llm_spans(...)`: traces with one LLM span each, in the bulk the detectors need.
- `execute(session_factory, sql, params)`: one write statement, past row-level security.
- `fetch(session_factory, sql, params)`: rows as dicts, past row-level security.
- `visible_to(session_factory, project_id, sql, params)`: rows as dicts, bound to one project.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from tests.rls.factories import create, create_project

SessionFactory = async_sessionmaker[AsyncSession]
Row = dict[str, Any]

# One trace per span, so a count of traces equals a count of spans. The ids are fresh hex.
_SEED_LLM_SPANS = text(
    """
    WITH ids AS (
        SELECT md5(random()::text || n::text) AS trace_id,
               left(md5(random()::text || n::text), 16) AS span_id
        FROM generate_series(1, :count) AS n
    ), new_traces AS (
        INSERT INTO traces (project_id, trace_id, started_at, ended_at, external_user_id)
        SELECT CAST(:project_id AS uuid), trace_id, :started_at,
               :started_at + interval '1 second', :user_id
        FROM ids
    )
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, input_tokens, output_tokens, cost_usd)
    SELECT CAST(:project_id AS uuid), trace_id, span_id, 'llm', 'call', :status, :started_at,
           :started_at + interval '1 second', 'openai', :model, 10, 5, :cost_usd
    FROM ids
    """
)


async def new_projects(session_factory: SessionFactory, count: int) -> list[uuid.UUID]:
    async with session_factory() as db:
        await bypass_rls(db)
        projects = [await create_project(db) for _ in range(count)]
        await db.commit()
    return projects


async def seed_row(session_factory: SessionFactory, table: str, project_id: uuid.UUID) -> Row:
    async with session_factory() as db:
        await bypass_rls(db)
        row = await create(db, table, project_id)
        await db.commit()
    return row


async def seed_llm_spans(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    count: int,
    *,
    started_at: datetime,
    model: str = "model-a",
    cost_usd: Decimal | None = None,
    user_id: str | None = None,
    status: str = "ok",
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            _SEED_LLM_SPANS,
            {
                "project_id": project_id,
                "count": count,
                "started_at": started_at,
                "model": model,
                "cost_usd": cost_usd,
                "user_id": user_id,
                "status": status,
            },
        )
        await db.commit()


async def execute(
    session_factory: SessionFactory, sql: str, params: dict[str, Any] | None = None
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(text(sql), params or {})
        await db.commit()


async def fetch(
    session_factory: SessionFactory, sql: str, params: dict[str, Any] | None = None
) -> list[Row]:
    """Rows past row-level security, as a worker sees them."""
    async with session_factory() as db:
        await bypass_rls(db)
        return [dict(row) for row in (await db.execute(text(sql), params or {})).mappings()]


async def visible_to(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[Row]:
    """Rows a session bound to `project_id` can read, as an API request sees them."""
    async with session_factory() as db:
        await bind_project(db, project_id)
        return [dict(row) for row in (await db.execute(text(sql), params or {})).mappings()]
