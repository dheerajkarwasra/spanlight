"""Application-owned resources close even when lifespan startup or its body fails."""

import asyncio
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import main
from app.config import Settings
from app.telemetry_losses import flush


async def _connection_pids(application: FastAPI) -> list[int]:
    engines = [application.state.engine, application.state.rate_limit_engine]
    if hasattr(application.state, "idempotency_engine"):
        engines.append(application.state.idempotency_engine)
    pids = []
    for engine in engines:
        async with engine.connect() as connection:
            pids.append(await connection.scalar(text("SELECT pg_backend_pid()")))
    return pids


async def _assert_connections_closed(engine: AsyncEngine, pids: list[int]) -> None:
    """A closed client's backend stays listed until its process exits, which lags the close under
    load; a connection the pool kept open never leaves, so a bounded wait still catches a leak."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    async with engine.connect() as connection:
        while True:
            remaining = await connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(:pids)"),
                {"pids": pids},
            )
            # Backend status is a snapshot per transaction: end it before reading again.
            await connection.rollback()
            if remaining == 0 or loop.time() >= deadline:
                break
            await asyncio.sleep(0.02)
    assert remaining == 0


@pytest.mark.parametrize("make_app", [main.create_app, main.create_gateway_app])
@pytest.mark.parametrize("exit_mode", ["normal", "failure", "cancelled"])
async def test_lifespan_closes_resources_and_preserves_body_failure(
    make_app: Callable[[Settings], FastAPI],
    exit_mode: str,
    settings: Settings,
    engine: AsyncEngine,
) -> None:
    application = make_app(settings)
    started = asyncio.Event()
    failure = RuntimeError("lifespan body failed")
    pids: list[int] = []
    flush_tasks: list[asyncio.Task[None]] = []

    async def run() -> None:
        async with application.router.lifespan_context(application):
            pids.extend(await _connection_pids(application))
            assert flush._flusher is not None
            flush_tasks.append(flush._flusher.task)
            started.set()
            if exit_mode == "failure":
                raise failure
            if exit_mode == "cancelled":
                await asyncio.Event().wait()

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(started.wait(), 10)
        if exit_mode == "cancelled":
            task.cancel("lifespan cancelled")
            with pytest.raises(asyncio.CancelledError, match="lifespan cancelled"):
                await task
        elif exit_mode == "failure":
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is failure
        else:
            await task
        assert application.state.gateway_http.is_closed
        assert flush_tasks[0].done()
        await _assert_connections_closed(engine, pids)
    finally:
        # Keep the deliberate pre-fix failure from leaking into the next regression case.
        await main._stop_gateway(application.state.gateway_runtime)
        await application.state.engine.dispose()
        await application.state.rate_limit_engine.dispose()
        if hasattr(application.state, "idempotency_engine"):
            await application.state.idempotency_engine.dispose()


@pytest.mark.parametrize("make_app", [main.create_app, main.create_gateway_app])
async def test_cleanup_failure_still_closes_pools_and_keeps_body_failure_in_chain(
    make_app: Callable[[Settings], FastAPI], settings: Settings, engine: AsyncEngine
) -> None:
    cleanup_failure = RuntimeError("upstream close failed")

    class FailingCloseTransport(httpx.AsyncBaseTransport):
        async def aclose(self) -> None:
            raise cleanup_failure

    application = make_app(settings, gateway_transport=FailingCloseTransport())
    failure = ValueError("lifespan body failed")
    pids: list[int] = []
    flush_tasks: list[asyncio.Task[None]] = []

    async def run() -> None:
        async with application.router.lifespan_context(application):
            pids.extend(await _connection_pids(application))
            assert flush._flusher is not None
            flush_tasks.append(flush._flusher.task)
            raise failure

    try:
        with pytest.raises(RuntimeError) as caught:
            await run()
        assert caught.value is cleanup_failure
        assert caught.value.__context__ is failure
        assert application.state.gateway_http.is_closed
        assert flush_tasks[0].done()
        await _assert_connections_closed(engine, pids)
    finally:
        await application.state.engine.dispose()
        await application.state.rate_limit_engine.dispose()
        if hasattr(application.state, "idempotency_engine"):
            await application.state.idempotency_engine.dispose()


@pytest.mark.parametrize("make_app", [main.create_app, main.create_gateway_app])
async def test_failed_runtime_start_closes_upstream_client_and_pools(
    make_app: Callable[[Settings], FastAPI],
    settings: Settings,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = make_app(settings)
    pids = await _connection_pids(application)
    clients: list[httpx.AsyncClient] = []
    real_build = main.build_http_client
    failure = RuntimeError("runtime construction failed")

    def capture_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client = real_build(*args, **kwargs)
        clients.append(client)
        return client

    def fail_runtime(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(main, "build_http_client", capture_client)
    monkeypatch.setattr(main, "build_runtime", fail_runtime)
    try:
        with pytest.raises(RuntimeError) as caught:
            async with application.router.lifespan_context(application):
                pytest.fail("startup must fail before accepting traffic")
        assert caught.value is failure
        assert clients[0].is_closed
        await _assert_connections_closed(engine, pids)
    finally:
        for client in clients:
            await client.aclose()
        await application.state.engine.dispose()
        await application.state.rate_limit_engine.dispose()
        if hasattr(application.state, "idempotency_engine"):
            await application.state.idempotency_engine.dispose()


@pytest.mark.parametrize("make_app", [main.create_app, main.create_gateway_app])
@hypothesis_settings(max_examples=8, deadline=None)
@given(message=st.text(max_size=40))
async def test_lifespan_preserves_arbitrary_body_exception_messages(
    make_app: Callable[[Settings], FastAPI], settings: Settings, message: str
) -> None:
    application = make_app(settings)
    failure = RuntimeError(message)

    async def run() -> None:
        async with application.router.lifespan_context(application):
            raise failure

    with pytest.raises(RuntimeError) as caught:
        await run()
    assert caught.value is failure
    assert str(caught.value) == message
    assert application.state.gateway_http.is_closed
