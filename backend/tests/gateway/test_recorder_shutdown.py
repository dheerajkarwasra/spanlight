"""Cancellation accounts for the whole span write, after transaction cleanup."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.gateway import record_write, recorder, span_write
from app.gateway.execute import execute
from app.ingest import pipeline
from tests.gateway.support import chat_request
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import (
    FakeProvider,
    fixture_text,
    logged,
    metric_delta,
    read_gateway_spans,
)


@pytest.fixture(autouse=True)
async def own_writers(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setattr(recorder, "_writers", None)
    yield
    await recorder.drain()


async def test_cancelled_queued_span_is_counted_once_after_join(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
) -> None:
    context = await gateway_context(keyed=False)
    writers = recorder._writers_for(context)
    await writers.slots.acquire()
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    try:
        result = await execute(chat_request(stream=False), context)
        assert result.status == 200
        await asyncio.sleep(0)  # writer reaches the occupied semaphore
        [task] = list(writers.pending)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await recorder.drain()
        assert errors.value == 1
        await recorder.drain()
        assert errors.value == 1
    finally:
        writers.slots.release()


@pytest.mark.parametrize("waiting", ["retry_sleep", "semaphore"])
async def test_lost_commit_ack_survives_cancel_before_retry_lookup(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    session_factory: "async_sessionmaker[AsyncSession]",
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    waiting: str,
) -> None:
    context = await gateway_context(keyed=False)
    writers = recorder._writers_for(context)
    dialect = context.sessions.kw["bind"].sync_engine.dialect
    real_commit = dialect.do_commit
    committed = False

    def lose_ack(connection: object) -> None:
        nonlocal committed
        real_commit(connection)
        if asyncio.current_task() in writers.pending and not committed:
            committed = True
            raise ConnectionResetError("commit acknowledgement lost")

    monkeypatch.setattr(dialect, "do_commit", lose_ack)
    reached_retry = asyncio.Event()

    async def retry_sleep(seconds: float) -> None:
        if waiting == "semaphore":
            await writers.slots.acquire()
            reached_retry.set()
            return
        reached_retry.set()
        await asyncio.Event().wait()

    context = dataclasses.replace(
        context,
        sleep=retry_sleep,
    )
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    try:
        await execute(chat_request(stream=False), context)
        await asyncio.wait_for(reached_retry.wait(), 2)
        await asyncio.sleep(0)
        [task] = list(writers.pending)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await recorder.drain()
        assert committed
        assert len(await read_gateway_spans(session_factory, context.project_id)) == 1
        assert errors.value == 0
        [diagnostic] = logged(caplog, "gateway.record_shutdown_uncertain")
        assert diagnostic["reason"] == "unacknowledged_commit"
    finally:
        if waiting == "semaphore" and reached_retry.is_set():
            writers.slots.release()


async def test_drain_timeout_joins_real_locked_transaction_before_return(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    context = await gateway_context(keyed=False)
    entered = asyncio.Event()
    real_ingest = pipeline.ingest_spans
    backend_pid = 0

    async def locked_ingest(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        nonlocal backend_pid
        backend_pid = await db.scalar(text("SELECT pg_backend_pid()"))
        entered.set()
        await db.execute(text("SELECT pg_advisory_xact_lock(150031)"))
        return await real_ingest(db, *args, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", locked_ingest)
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    async with context.sessions() as blocker, blocker.begin():
        await blocker.execute(text("SELECT pg_advisory_xact_lock(150031)"))
        await execute(chat_request(stream=False), context)
        await asyncio.wait_for(entered.wait(), 2)
        writers = recorder._writers
        assert writers is not None
        tasks = tuple(writers.pending)
        try:
            await recorder.drain(timeout_s=0.02)
            assert all(task.done() for task in tasks)
            assert errors.value == 1
            async with context.sessions() as observer:
                active = await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE pid=:pid AND xact_start IS NOT NULL"
                    ),
                    {"pid": backend_pid},
                )
                assert active == 0
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_confirmed_commit_before_session_close_is_not_uncertain_or_lost(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = await gateway_context(keyed=False)
    closing = asyncio.Event()
    release = asyncio.Event()
    real_close = AsyncSession.close

    async def held_close(session: AsyncSession) -> None:
        await real_close(session)
        if session.info.get("shutdown_writer"):
            closing.set()
            await release.wait()

    real_ingest = pipeline.ingest_spans

    async def mark_writer(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        db.info["shutdown_writer"] = True
        return await real_ingest(db, *args, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", mark_writer)
    monkeypatch.setattr(AsyncSession, "close", held_close)
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    try:
        await execute(chat_request(stream=False), context)
        await asyncio.wait_for(closing.wait(), 2)
        writers = recorder._writers
        assert writers is not None
        [task] = list(writers.pending)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await recorder.drain()
        assert errors.value == 0
        assert logged(caplog, "gateway.record_shutdown_uncertain") == []
    finally:
        release.set()


@pytest.mark.parametrize("path", ["write_error", "settlement_only"])
async def test_already_counted_loss_is_not_counted_again_while_settlement_is_cancelled(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    context = await gateway_context(keyed=False)
    settling = asyncio.Event()

    async def held_settle(*args: Any, **kwargs: Any) -> None:
        settling.set()
        await asyncio.Event().wait()

    async def refused_ingest(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("injected refusal")

    def refused_prepare(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("injected payload preparation failure")

    monkeypatch.setattr(record_write, "settle_unstored", held_settle)
    monkeypatch.setattr(recorder, "settle_unstored", held_settle)
    if path == "write_error":
        monkeypatch.setattr(span_write, "ingest_spans", refused_ingest)
    else:
        monkeypatch.setattr(recorder, "prepare_span_payloads", refused_prepare)
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    await execute(chat_request(stream=False), context)
    await asyncio.wait_for(settling.wait(), 2)
    assert errors.value == 1
    await recorder.drain(timeout_s=0)
    assert errors.value == 1
