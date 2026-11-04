"""Bounded real-lock/cancellation cases on dedicated PostgreSQL connections."""

import asyncio

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.observability import RATE_LIMIT_UNAVAILABLE
from app.core.ratelimit import INGEST_LIMITER, take_token
from app.db.session import create_engine, create_session_factory
from tests.conftest import admin_database_url

pytestmark = pytest.mark.serial


@given(
    key_suffix=st.integers(min_value=0, max_value=10000),
    cancel=st.booleans(),
    delay_ms=st.integers(min_value=5, max_value=35),
)
@hypothesis_settings(max_examples=8, deadline=None, derandomize=True)
def test_bucket_lock_and_cancellation_properties(
    app_database_url: str,
    key_suffix: int,
    cancel: bool,
    delay_ms: int,
) -> None:
    async def exercise() -> None:
        engine = create_engine(
            app_database_url,
            pool_size=2,
            max_overflow=0,
            pre_ping=False,
            isolation_level="AUTOCOMMIT",
            lock_timeout_ms=60,
            statement_timeout_ms=150,
        )
        admin = create_async_engine(admin_database_url())
        sessions = create_session_factory(engine)
        key = f"property:{key_suffix}:{cancel}:{delay_ms}"
        counter = RATE_LIMIT_UNAVAILABLE.labels("ingest")
        try:
            assert await take_token(sessions, INGEST_LIMITER, key, scope="ingest") is None
            before = counter._value.get()
            async with admin.begin() as lock:
                await lock.execute(
                    text("SELECT key FROM rate_limit_buckets WHERE key=:key FOR UPDATE"),
                    {"key": key},
                )
                task = asyncio.create_task(
                    take_token(sessions, INGEST_LIMITER, key, scope="ingest")
                )
                # Cancel before the other-key call: under load that call can outlast the 60 ms
                # lock timeout, so the waiter would fail open before the cancel arrives.
                await asyncio.sleep(delay_ms / 1000)
                if cancel:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    else:
                        raise AssertionError("Cancellation must propagate")
                    assert counter._value.get() == before
                else:
                    assert await asyncio.wait_for(task, timeout=1) is None
                    assert counter._value.get() == before + 1
                # The held row lock never blocks a different key.
                assert (
                    await take_token(sessions, INGEST_LIMITER, key + ":other", scope="ingest")
                    is None
                )
            assert await take_token(sessions, INGEST_LIMITER, key, scope="ingest") is None
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(exercise())
