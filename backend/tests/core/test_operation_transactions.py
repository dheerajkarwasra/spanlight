"""Business transaction atomicity and measured mixed operation/replay behavior."""

import statistics
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ProblemError
from app.core.idempotency_store import Reservation, StoredResponse, complete_operation, reserve


async def test_rolled_back_business_work_cannot_leave_a_completed_operation_receipt(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    reservation = await reserve(session_factory, "user:rollback", "job", b"a" * 32)
    assert isinstance(reservation, Reservation)
    async with session_factory() as db:
        await db.execute(text("INSERT INTO jobs (kind, payload) VALUES ('task14', '{}')"))
        await complete_operation(
            db, StoredResponse(202, {"queued": True}, "application/json"), reservation=reservation
        )
        await db.rollback()
    async with session_factory() as db:
        assert await db.scalar(text("SELECT count(*) FROM jobs WHERE kind = 'task14'")) == 0
        assert (
            await db.scalar(text("SELECT status FROM idempotency_keys WHERE key = 'job'")) is None
        )
    with pytest.raises(ProblemError) as refused:
        await reserve(session_factory, "user:rollback", "job", b"a" * 32)
    assert refused.value.code == "IDEMPOTENCY_IN_PROGRESS"


async def test_mixed_new_and_replayed_operations_commit_one_job_each(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Measure real round trips while asserting duplicate requests do not add domain work."""
    receipt_ms: list[float] = []
    lookup_ms: list[float] = []
    for index in range(30):
        key = f"mixed-{index}"
        reservation = await reserve(session_factory, "user:mixed", key, b"a" * 32)
        assert isinstance(reservation, Reservation)
        async with session_factory() as db:
            await db.execute(text("INSERT INTO jobs (kind, payload) VALUES ('task14', '{}')"))
            started = time.perf_counter()
            await complete_operation(
                db,
                StoredResponse(202, {"queued": index}, "application/json"),
                reservation=reservation,
            )
            receipt_ms.append((time.perf_counter() - started) * 1000)
            await db.commit()
        started = time.perf_counter()
        answer = await reserve(session_factory, "user:mixed", key, b"a" * 32)
        lookup_ms.append((time.perf_counter() - started) * 1000)
        assert isinstance(answer, StoredResponse)
        assert answer.body == {"queued": index}
    async with session_factory() as db:
        assert await db.scalar(text("SELECT count(*) FROM jobs WHERE kind = 'task14'")) == 30
    print(
        f"Task14 mixed30 receipt median={statistics.median(receipt_ms):.3f}ms "
        f"replay lookup median={statistics.median(lookup_ms):.3f}ms"
    )
