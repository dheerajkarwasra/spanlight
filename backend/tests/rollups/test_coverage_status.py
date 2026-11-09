"""`coverage_active` reads whether aggregate coverage was activated on this database."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from app.config import Settings
from app.db.migrations import upgrade_to_head
from app.db.session import create_engine, create_session_factory
from app.jobs.worker import Worker
from app.main import create_app
from app.rollups.activation import activate_aggregate_coverage
from app.rollups.coverage_status import coverage_active
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database


@contextmanager
def unactivated_database() -> Iterator[str]:
    """A disposable database migrated to head, with coverage never activated (owner URL)."""
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _base_database_url()
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    with owned_database(_libpq(base), name):
        upgrade_to_head(url)
        yield url


async def _active(url: str) -> bool:
    engine = create_engine(url, pool_size=1)
    try:
        async with engine.connect() as connection:
            return await coverage_active(connection)
    finally:
        await engine.dispose()


async def test_coverage_inactive_before_activation(database_server: None) -> None:
    with unactivated_database() as url:
        assert await _active(url) is False


async def test_coverage_active_after_activation(database_server: None) -> None:
    with unactivated_database() as url:
        activate_aggregate_coverage(url, fresh_install=False)
        assert await _active(url) is True


Starter = Callable[[Settings], Awaitable[None]]
FIX = "spanlight activate-aggregate-coverage --writers-drained"


def _inactive_warnings(caplog: pytest.LogCaptureFixture) -> list[tuple[str, object]]:
    """(level, fix) of every `aggregate_coverage_inactive` event logged during the test."""
    return [
        (record.levelname, record.msg["fix"])
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "aggregate_coverage_inactive"
    ]


async def _start_api(settings: Settings) -> None:
    root = logging.getLogger()
    handlers = root.handlers[:]
    application = create_app(settings)
    root.handlers = handlers  # create_app's logging setup replaces them, caplog's included
    async with application.router.lifespan_context(application):
        pass


async def _start_worker(settings: Settings) -> None:
    engine = create_engine(settings.database_url, pool_size=2)
    try:
        stop = asyncio.Event()
        stop.set()  # start, log, and stop before the first iteration
        await Worker(create_session_factory(engine), settings, handlers={}).run_forever(stop)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("start", [_start_api, _start_worker], ids=["api", "worker"])
async def test_start_warns_once_when_coverage_is_inactive(
    start: Starter, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with unactivated_database() as url:
        await start(settings.model_copy(update={"database_url": url}))
    assert _inactive_warnings(caplog) == [("WARNING", FIX)]


@pytest.mark.parametrize("start", [_start_api, _start_worker], ids=["api", "worker"])
async def test_start_is_quiet_when_coverage_is_active(
    start: Starter, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    await start(settings)  # the harness database is activated as a fresh install
    assert _inactive_warnings(caplog) == []
