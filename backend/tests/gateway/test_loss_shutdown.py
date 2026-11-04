"""Final loss flushing reports incomplete persistence and retains recoverable counts."""

import socket

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway import span_losses
from app.telemetry_losses import flush
from app.telemetry_losses.counter import LossCounter
from tests.gateway.loss_support import read_losses, two_projects
from tests.gateway_support import logged


async def test_blocked_final_flush_reports_incomplete_then_recovers(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [project, _] = await two_projects(session_factory)
    counter = LossCounter()
    monkeypatch.setattr(span_losses, "_counter", counter)
    monkeypatch.setattr(flush, "_flusher", None)
    span_losses.lose(project, "error")
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(text("LOCK TABLE telemetry_losses_hourly IN ACCESS EXCLUSIVE MODE"))
        flush.start(session_factory, counter)
        await flush.stop(timeout_s=0.02)
        [entry] = logged(caplog, "process.shutdown_incomplete")
        assert (entry["phase"], entry["reason"]) == ("loss_flush", "flush_timeout")
    assert await flush.flush_once(session_factory, counter) == 1
    assert sum((await read_losses(session_factory, project)).values()) == 1


async def test_unavailable_database_does_not_turn_finished_flush_into_success(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [project, _] = await two_projects(session_factory)
    counter = LossCounter()
    monkeypatch.setattr(span_losses, "_counter", counter)
    monkeypatch.setattr(flush, "_flusher", None)
    span_losses.lose(project, "error")
    with socket.socket() as closed_port:
        closed_port.bind(("127.0.0.1", 0))  # bound but not listening: real connection refusal
        engine = create_async_engine(
            session_factory.kw["bind"].url.set(host="127.0.0.1", port=closed_port.getsockname()[1]),
            connect_args={"connect_timeout": 1},
        )
        try:
            flush.start(async_sessionmaker(engine), counter)
            assert not await flush.stop(timeout_s=5)
            failures = logged(caplog, "telemetry_losses.flush_failed")
            assert any(entry.get("stage") == "flush" for entry in failures)
            assert flush.pending_tasks() == ()
            assert counter.pending_count() == 1
        finally:
            await engine.dispose()
    assert await flush.flush_once(session_factory, counter) == 1
    assert counter.pending_count() == 0
    assert sum((await read_losses(session_factory, project)).values()) == 1
