"""Real PostgreSQL limiter deadlines preserve ingestion's fail-open policy."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.observability import RATE_LIMIT_UNAVAILABLE
from app.core.ratelimit import INGEST_LIMITER, take_token
from app.core.throttle import check_and_record
from app.gateway.limits import key_bucket_key, per_minute_bucket
from app.main import create_app, create_gateway_app
from tests.conftest import admin_database_url
from tests.gateway_fixtures import GatewayWorkspace
from tests.helpers import Workspace, create_api_key, new_trace_id, span
from tests.ingest.support import ingest_native

pytestmark = pytest.mark.serial


async def test_locked_bucket_ingest_fails_open_with_metric(
    client: httpx.AsyncClient,
    workspace: Workspace,
    app: Any,
) -> None:
    assert (
        await ingest_native(client, workspace, [span(trace_id=new_trace_id())])
    ).status_code == 200
    other = await create_api_key(workspace.owner, workspace.project_id)
    unrelated = replace(workspace, api_key=other["secret"])
    engine = create_async_engine(admin_database_url())
    counter = RATE_LIMIT_UNAVAILABLE.labels("ingest")
    before = counter._value.get()
    try:
        async with engine.begin() as lock:
            await lock.execute(
                text("SELECT key FROM rate_limit_buckets WHERE key LIKE 'ingest:key:%' FOR UPDATE")
            )
            unrelated_response = await asyncio.wait_for(
                ingest_native(client, unrelated, [span(trace_id=new_trace_id())]), timeout=0.8
            )
            assert unrelated_response.status_code == 200
            started = time.monotonic()
            response = await asyncio.wait_for(
                ingest_native(client, workspace, [span(trace_id=new_trace_id())]),
                timeout=1.0,
            )
            assert response.status_code == 200
            assert time.monotonic() - started < 0.8
            assert counter._value.get() == before + 1
        async with app.state.rate_limit_engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize("cancel_after", [0.01, 0.035, 0.065])
async def test_cancelled_bucket_check_propagates_and_pool_recovers(
    app: Any,
    cancel_after: float,
) -> None:
    sessions = app.state.rate_limit_session_factory
    key = f"cancel:{cancel_after}"
    assert await take_token(sessions, INGEST_LIMITER, key, scope="ingest") is None
    engine = create_async_engine(admin_database_url())
    counter = RATE_LIMIT_UNAVAILABLE.labels("ingest")
    before = counter._value.get()
    try:
        async with engine.begin() as lock:
            await lock.execute(
                text("SELECT key FROM rate_limit_buckets WHERE key=:key FOR UPDATE"), {"key": key}
            )
            task = asyncio.create_task(take_token(sessions, INGEST_LIMITER, key, scope="ingest"))
            await asyncio.sleep(cancel_after)
            started = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert time.monotonic() - started < 1
            assert counter._value.get() == before
            assert (
                await take_token(sessions, INGEST_LIMITER, key + ":unrelated", scope="ingest")
                is None
            )
        assert await take_token(sessions, INGEST_LIMITER, key, scope="ingest") is None
        async with engine.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE usename='spanlight_app_test' AND state='idle in transaction'"
                    )
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()


async def test_limiter_statement_timeout_and_connection_reuse(app: Any) -> None:
    async with app.state.rate_limit_engine.connect() as connection:
        assert (await connection.execute(text("SHOW lock_timeout"))).scalar_one() == "100ms"
        assert (await connection.execute(text("SHOW statement_timeout"))).scalar_one() == "250ms"
        started = time.monotonic()
        with pytest.raises(DBAPIError):
            await connection.execute(text("SELECT pg_sleep(2)"))
        assert time.monotonic() - started < 1
        await connection.rollback()
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


async def test_auth_advisory_lock_error_does_not_admit(app: Any) -> None:
    engine = create_async_engine(admin_database_url())
    try:
        async with engine.begin() as lock:
            await lock.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended('password_reset:locked', 0))")
            )
            async with app.state.session_factory() as session:
                await session.execute(text("SET LOCAL lock_timeout='50ms'"))
                with pytest.raises(DBAPIError):
                    await check_and_record(
                        session, "password_reset", "locked", 3, timedelta(hours=1)
                    )
                await session.rollback()
        async with engine.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM throttle_events "
                        "WHERE scope='password_reset' AND key='locked'"
                    )
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()


async def test_gateway_bucket_timeout_reaches_body_validation(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
) -> None:
    workspace = gateway_workspace
    edited = await workspace.owner.patch(
        f"/api/v1/projects/{workspace.project_id}/gateway/keys/{workspace.gateway_key_id}",
        json={"rpm_limit": 1},
    )
    assert edited.status_code == 200
    key = key_bucket_key(workspace.gateway_key_id)
    assert await gateway_app.state.gateway_runtime.limiter.take(per_minute_bucket(1), key) is None
    counter = RATE_LIMIT_UNAVAILABLE.labels("gateway")
    before = counter._value.get()
    engine = create_async_engine(admin_database_url())
    try:
        async with engine.begin() as lock:
            await lock.execute(
                text("SELECT key FROM rate_limit_buckets WHERE key=:key FOR UPDATE"), {"key": key}
            )
            response = await asyncio.wait_for(
                workspace.gw.post("/gw/v1/chat/completions", json={}),
                timeout=1,
            )
            assert response.status_code == 400
            assert counter._value.get() == before + 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize("make_app", [create_app, create_gateway_app])
async def test_custom_sql_limits_reach_both_application_modes(
    settings: Settings,
    make_app: Callable[[Settings], FastAPI],
) -> None:
    application = make_app(
        settings.model_copy(
            update={
                "rate_limit_lock_timeout_ms": 70,
                "rate_limit_statement_timeout_ms": 180,
            }
        )
    )
    async with application.router.lifespan_context(application):
        async with application.state.rate_limit_engine.connect() as connection:
            assert (await connection.execute(text("SHOW lock_timeout"))).scalar_one() == "70ms"
            assert (
                await connection.execute(text("SHOW statement_timeout"))
            ).scalar_one() == "180ms"
        async with application.state.engine.connect() as connection:
            assert (await connection.execute(text("SHOW lock_timeout"))).scalar_one() == "0"
