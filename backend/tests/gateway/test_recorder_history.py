"""Later rollback cannot erase an earlier unresolved COMMIT."""

import asyncio
import dataclasses
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.gateway import recorder, span_write
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


@pytest.mark.parametrize("resolve", [False, True])
async def test_retry_rollback_retains_uncertainty_until_positive_duplicate_confirmation(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    resolve: bool,
) -> None:
    monkeypatch.setattr(recorder, "_writers", None)
    context = await gateway_context(keyed=False)
    writers = recorder._writers_for(context)
    dialect = context.sessions.kw["bind"].sync_engine.dialect
    real_commit = dialect.do_commit
    first_commit = True
    attempts = 0
    ready = asyncio.Event()
    release = asyncio.Event()
    real_lookup = span_write._already_stored

    def uncertain_commit(connection: Any) -> None:
        nonlocal first_commit
        if asyncio.current_task() in writers.pending and first_commit:
            first_commit = False
            if resolve:
                real_commit(connection)
            raise ConnectionResetError("acknowledgement unavailable")
        real_commit(connection)

    async def second_attempt_rolls_back(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        outcome = await pipeline.ingest_spans(db, *args, **kwargs)
        if attempts == 2:
            raise ConnectionResetError("later attempt rolls back before commit")
        return outcome

    async def lookup(db: AsyncSession, *args: Any, **kwargs: Any) -> bool:
        found = await real_lookup(db, *args, **kwargs)
        if found:
            db.info["duplicate_confirmed"] = True
        return found

    real_close = AsyncSession.close

    async def hold_confirmed_close(db: AsyncSession) -> None:
        await real_close(db)
        if db.info.get("duplicate_confirmed"):
            ready.set()
            await release.wait()

    sleeps = 0

    async def retry_sleep(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            ready.set()
            await release.wait()

    monkeypatch.setattr(dialect, "do_commit", uncertain_commit)
    monkeypatch.setattr(span_write, "ingest_spans", second_attempt_rolls_back)
    monkeypatch.setattr(span_write, "_already_stored", lookup)
    monkeypatch.setattr(AsyncSession, "close", hold_confirmed_close)
    context = dataclasses.replace(context, sleep=retry_sleep)
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    try:
        await execute(chat_request(stream=False), context)
        await asyncio.wait_for(ready.wait(), 2)
        [task] = list(writers.pending)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        state = await recorder.shutdown(cancel_at=asyncio.get_running_loop().time())
        assert state.joined
        assert state.accounting_complete is resolve
        assert errors.value == 0
        rows = await read_gateway_spans(session_factory, context.project_id)
        if resolve:
            assert len(rows) == 1
            assert attempts == 1
            assert logged(caplog, "gateway.record_shutdown_uncertain") == []
        else:
            assert rows == []
            assert attempts == 2
            assert len(logged(caplog, "gateway.record_shutdown_uncertain")) == 1
    finally:
        release.set()
        await recorder.drain(timeout_s=0)
