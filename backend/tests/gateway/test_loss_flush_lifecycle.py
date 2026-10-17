"""Every process that can lose gateway spans runs the loss flush, and flushes once more at exit.

The api's lifespan, the standalone gateway's and the worker's `main` each start the flush and
stop it at shutdown. The api and the gateway stop it after the recorder's drain (which may still
lose spans) and before their engines close; the worker stops it before its engine closes.
"""

import asyncio
import contextlib
import signal
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import main
from app.config import Settings
from app.gateway import span_losses
from app.jobs import worker
from app.telemetry_losses import flush
from app.telemetry_losses.counter import LossCounter
from tests.gateway.loss_support import read_losses, two_projects


@pytest.fixture(autouse=True)
def own_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A counter of this test's own, and no flusher left over from another test."""
    monkeypatch.setattr(span_losses, "_counter", LossCounter())
    monkeypatch.setattr(flush, "_flusher", None)


def _by_reason(rows: dict[Any, int]) -> dict[tuple[str, str], int]:
    return {(source, reason): n for (source, reason, _), n in rows.items()}


@pytest.mark.parametrize("make_app", [main.create_app, main.create_gateway_app])
async def test_each_lifespan_flushes_the_losses_of_its_recorder_drain_at_shutdown(
    make_app: Callable[[Settings], Any],
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [project_id, _] = await two_projects(session_factory)
    real_close = main.close_runtime

    async def close_losing_one(runtime: Any) -> None:
        await real_close(runtime)
        span_losses.lose(project_id, "overflow")  # a span the recorder's drain lost

    monkeypatch.setattr(main, "close_runtime", close_losing_one)
    application = make_app(settings)

    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as http,
    ):
        assert (await http.get("/health/live")).status_code == 200

    rows = await read_losses(session_factory, project_id)
    assert _by_reason(rows) == {("gateway", "overflow"): 1}


async def test_the_worker_runs_the_flush_and_flushes_at_exit(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [project_id, _] = await two_projects(session_factory)
    flushing: list[bool] = []

    async def run_forever(_: worker.Worker, stop: asyncio.Event) -> None:
        flushing.append(flush._flusher is not None)
        span_losses.lose(project_id, "error")  # an eval run's span the worker could not store

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "serve_metrics", lambda *_: contextlib.nullcontext())
    monkeypatch.setattr(worker.Worker, "run_forever", run_forever)
    loop = asyncio.get_running_loop()
    try:
        await worker.main()
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)

    assert flushing == [True]
    assert flush._flusher is None
    rows = await read_losses(session_factory, project_id)
    assert _by_reason(rows) == {("gateway", "error"): 1}
