"""Fault injection for the ingestion writes.

`inject_after` runs a fault on the request's own session right after one step of
`app.ingest.store` (`_upsert_traces`, `_upsert_spans` or `_recompute_rollups`), once: a replay of
the same batch afterwards runs clean. The faults stand for what a database can do to a write in
flight: cancel a statement on its timeout, fail it, or drop the connection before the commit.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest import store
from tests.conftest import admin_database_url

Fault = Callable[[AsyncSession], Awaitable[None]]


def inject_after(monkeypatch: pytest.MonkeyPatch, step: str, fault: Fault) -> None:
    """Run `fault` on the request's session after `step` of `app.ingest.store`, the first time."""
    original = getattr(store, step)
    fired: list[bool] = []

    async def faulty(session: AsyncSession, *args: Any, **kwargs: Any) -> None:
        await original(session, *args, **kwargs)
        if not fired:
            fired.append(True)
            await fault(session)

    monkeypatch.setattr(store, step, faulty)


async def statement_timeout(session: AsyncSession) -> None:
    """A statement that outlives a 50 ms `statement_timeout`: Postgres cancels it."""
    await session.execute(text("SELECT set_config('statement_timeout', '50', true)"))
    await session.execute(text("SELECT pg_sleep(1)"))


async def operational_error(_: AsyncSession) -> None:
    """A database failure that is not a timeout, as SQLAlchemy reports it."""
    raise OperationalError("injected", None, RuntimeError("injected database failure"))


async def kill_connection(session: AsyncSession) -> None:
    """Terminate the request's own backend from another connection, before it commits."""
    pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    await terminate_backend(pid)


def admin_libpq_url() -> str:
    return admin_database_url().replace("postgresql+psycopg://", "postgresql://")


async def terminate_backend(pid: int) -> None:
    """End backend `pid` and wait (up to 5 s) until it is gone."""
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        cursor = await admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))
        row = await cursor.fetchone()
        assert row is not None and row[0] is True, f"backend {pid} was not terminated"


async def delete_project(project_id: str) -> None:
    """Delete a project (and, through its foreign keys, its keys and telemetry) and commit."""
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        await admin.execute("DELETE FROM projects WHERE id = %s", (project_id,))
