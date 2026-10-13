"""A deterministic worst case for explains that race on one organization's budget.

Without help, whether two racing requests interleave between the budget sum and the reservation
insert depends on timing, so a broken lock could pass a test by luck. `RaceGate` removes the
luck: it holds each request right after its sum and right before its insert until every other
racing request is quiet, meaning held at one of these gates, blocked on a Postgres advisory lock
(read from `pg_locks`), past its insert, or finished. With the per-organization lock working,
the request inside the lock is the only one that can move, so nothing changes. With the lock
missing, or the sum and the insert in separate transactions, every request reaches its sum
before any reservation is written, so they all pass the check: the test goes red every time.

Blocked requests are only visible in `pg_locks`, so the gate polls it every few milliseconds.
"""

import asyncio
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.insights import explain_queries

POLL_SECONDS = 0.005
WAIT_SECONDS = 10.0
# `pg_locks` covers the whole server; only this database's waiters are the racing requests.
_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
)


class RaceGate:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], requests: int) -> None:
        self._sessions = sessions
        self._requests = requests
        self._held = 0
        self._past_insert = 0
        self.tasks: list[asyncio.Task[httpx.Response]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Gate `month_to_date_cost` (after the sum) and `insert_reservation` (before it)."""
        summed = explain_queries.month_to_date_cost
        insert = explain_queries.insert_reservation

        async def gated_sum(db: AsyncSession, org_id: uuid.UUID, since: datetime) -> Decimal:
            total = await summed(db, org_id, since)
            await self._hold()
            return total

        async def gated_insert(db: AsyncSession, **kwargs: Any) -> uuid.UUID:
            await self._hold()
            row_id = await insert(db, **kwargs)
            self._past_insert += 1
            return row_id

        monkeypatch.setattr(explain_queries, "month_to_date_cost", gated_sum)
        monkeypatch.setattr(explain_queries, "insert_reservation", gated_insert)

    async def _hold(self) -> None:
        self._held += 1
        try:
            async with asyncio.timeout(WAIT_SECONDS):
                # Polled: a request blocked on the lock is only visible in `pg_locks`.
                while await self._quiet() < self._requests:  # noqa: ASYNC110
                    await asyncio.sleep(POLL_SECONDS)
        finally:
            self._held -= 1

    async def _quiet(self) -> int:
        async with self._sessions() as db:
            blocked = int(await db.scalar(_LOCK_WAITERS) or 0)
        finished = sum(task.done() for task in self.tasks)
        return self._held + blocked + self._past_insert + finished
