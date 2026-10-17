"""Gateway span losses reach `telemetry_losses_hourly` through the 60-second flush.

The recorder counts each lost span in the process's `LossCounter` by project and reason. The
flush drains it and writes each project's counts in a transaction of its own, bound to that
project and never with the row-level security bypass (an api process never sets it). A project
whose write fails for a reason that may pass keeps its counts for the next flush; one whose
write can never succeed (the project was deleted) is dropped and logged. The last flush runs at
shutdown, after the recorder's own drain, and gives up after its budget when the database is down.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.rls import bind_project
from app.gateway import call, recorder, span_losses, span_write
from app.ingest import pipeline
from app.ingest.pipeline import IngestOutcome, ProjectGoneError
from app.ingest.schemas import RejectedSpan
from app.rollups.compute import floor_hour
from app.telemetry_losses import flush
from app.telemetry_losses.counter import LossCounter
from tests.gateway.loss_support import read_losses, two_projects
from tests.gateway.support import CHAT_BODY
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, drain_recorder, fixture_text, logged

CALL_PATH = "/gw/v1/chat/completions"
LOST_HOUR = datetime(2026, 10, 16, 9, tzinfo=UTC)


@pytest.fixture
def gateway_settings(gateway_settings: Settings) -> Settings:
    """One span written at a time and one waiting at most, so a second span overflows."""
    return gateway_settings.model_copy(
        update={"gateway_record_backlog": 1, "gateway_record_concurrency": 1}
    )


@pytest.fixture(autouse=True)
async def own_flusher(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """No flusher left over from another test, and fresh recorder writers."""
    monkeypatch.setattr(flush, "_flusher", None)
    monkeypatch.setattr(recorder, "_writers", None)
    yield
    await drain_recorder()


class SpyingSessions:
    """The real sessions, recording the RLS settings each transaction had when it committed."""

    def __init__(self, real: async_sessionmaker[AsyncSession]) -> None:
        self.real = real
        self.settings: list[tuple[str | None, str | None]] = []

    def __call__(self) -> AsyncSession:
        session = self.real()
        real_commit = session.commit

        async def commit() -> None:
            row = (
                await session.execute(
                    text(
                        "SELECT current_setting('app.project_id', true) AS project, "
                        "current_setting('app.bypass_rls', true) AS bypass"
                    )
                )
            ).one()
            self.settings.append((row.project, row.bypass))
            await real_commit()

        session.commit = commit  # type: ignore[method-assign]
        return session


class FlakySessions:
    """The real sessions, except that the first one fails to open with `error`."""

    def __init__(self, real: async_sessionmaker[AsyncSession], error: Exception) -> None:
        self.real = real
        self.error: Exception | None = error

    def __call__(self) -> AsyncSession:
        error, self.error = self.error, None
        if error is not None:
            raise error
        return self.real()


class SleepingSessions:
    """Sessions that never open: entering one waits 30 s, a database that does not answer."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self

    async def __aenter__(self) -> AsyncSession:
        await asyncio.sleep(30)
        raise AssertionError("not reached")

    async def __aexit__(self, *_: object) -> None: ...


async def test_overflow_error_and_rejected_losses_reach_the_table(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = LossCounter()
    monkeypatch.setattr(span_losses, "_counter", counter)
    for _ in range(6):
        fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    before = floor_hour(datetime.now(UTC))

    async def post_all(count: int) -> None:
        for _ in range(count):
            assert (await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)).status_code == 200

    # Overflow: the first write holds the only slot and the backlog of one, so two are dropped.
    release = asyncio.Event()
    real_ingest = pipeline.ingest_spans

    async def held_ingest(*args: Any, **kwargs: Any) -> Any:
        await release.wait()
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", held_ingest)
    await post_all(3)
    release.set()
    await drain_recorder()

    # Rejected: ingestion refuses the span.
    async def refusing_ingest(db: AsyncSession, target: Any, *_: Any, **__: Any) -> IngestOutcome:
        await bind_project(db, target.project_id)  # as ingestion does; the key's rows need it
        refused = RejectedSpan(index=0, span_id=None, reason="span: could not be processed")
        return IngestOutcome(rejected=[refused])

    monkeypatch.setattr(span_write, "ingest_spans", refusing_ingest)
    await post_all(1)
    await drain_recorder()

    # Error: once a write that fails for good, once a span that cannot even be built.
    async def gone_ingest(*_: Any, **__: Any) -> IngestOutcome:
        raise ProjectGoneError("the project was deleted")

    def unbuildable(*_: Any, **__: Any) -> dict[str, Any]:
        raise ValueError("cannot build the span")

    monkeypatch.setattr(span_write, "ingest_spans", gone_ingest)
    await post_all(1)
    monkeypatch.setattr(call, "build_span", unbuildable)
    await post_all(1)

    written = await flush.flush_once(session_factory, counter)

    after = floor_hour(datetime.now(UTC))
    rows = await read_losses(session_factory, gateway_workspace.project_id)
    assert written == 5
    assert {(source, reason): n for (source, reason, _), n in rows.items()} == {
        ("gateway", "overflow"): 2,
        ("gateway", "rejected"): 1,
        ("gateway", "error"): 2,
    }
    assert {hour for (_, _, hour) in rows} <= {before, after}
    assert counter.drain() == {}


async def test_flush_over_two_projects_writes_each_under_its_own_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await two_projects(session_factory)
    counter = LossCounter()
    counter.add(first, "gateway", "overflow", LOST_HOUR, 3)
    counter.add(first, "gateway", "error", LOST_HOUR)
    counter.add(second, "gateway", "rejected", LOST_HOUR, 2)
    spy = SpyingSessions(session_factory)

    written = await flush.flush_once(spy, counter)

    assert written == 6
    assert [project for project, _ in spy.settings] == [str(first), str(second)]
    assert all(bypass in (None, "") for _, bypass in spy.settings)
    assert await read_losses(session_factory, first) == {
        ("gateway", "overflow", LOST_HOUR): 3,
        ("gateway", "error", LOST_HOUR): 1,
    }
    assert await read_losses(session_factory, second) == {("gateway", "rejected", LOST_HOUR): 2}


@pytest.mark.parametrize(
    ("error", "sqlstate"),
    [
        (ConnectionRefusedError("the database is down, said 'sk-secret'"), None),  # transient
        # Not transient, and not a deleted project: kept too, in case it is a passing state.
        (ProgrammingError("INSERT", {}, InsufficientPrivilege("denied 'sk-secret'")), "42501"),
    ],
)
async def test_failed_project_entries_are_restored_and_retried(
    error: Exception,
    sqlstate: str | None,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    first, second = await two_projects(session_factory)
    counter = LossCounter()
    counter.add(first, "gateway", "overflow", LOST_HOUR, 2)
    counter.add(second, "gateway", "error", LOST_HOUR)
    flaky = FlakySessions(session_factory, error)

    assert await flush.flush_once(flaky, counter) == 1
    assert await read_losses(session_factory, first) == {}
    assert await read_losses(session_factory, second) == {("gateway", "error", LOST_HOUR): 1}
    [failure] = logged(caplog, "telemetry_losses.flush_failed")
    assert (failure["error_type"], failure["sqlstate"]) == (type(error).__name__, sqlstate)
    assert (failure["stage"], failure["kept"]) == ("flush", True)
    assert "sk-secret" not in str(failure)

    assert await flush.flush_once(flaky, counter) == 2
    assert await read_losses(session_factory, first) == {("gateway", "overflow", LOST_HOUR): 2}
    assert await read_losses(session_factory, second) == {("gateway", "error", LOST_HOUR): 1}


async def test_a_deleted_project_is_dropped_not_retried(
    session_factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    counter = LossCounter()
    counter.add(uuid.uuid4(), "gateway", "error", LOST_HOUR)

    assert await flush.flush_once(session_factory, counter) == 0

    [failure] = logged(caplog, "telemetry_losses.flush_failed")
    assert (failure["sqlstate"], failure["kept"]) == ("23503", False)
    assert counter.drain() == {}


async def test_shutdown_flush_is_bounded_when_the_database_is_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    counter = LossCounter()
    key = (uuid.uuid4(), "gateway", "overflow", LOST_HOUR)
    counter.add(*key)
    flush.start(SleepingSessions(), counter)  # type: ignore[arg-type]

    started = time.monotonic()
    await flush.stop(timeout_s=5)

    assert time.monotonic() - started < 6
    [failure] = logged(caplog, "telemetry_losses.flush_failed")
    assert {k: failure[k] for k in ("stage", "error_type", "kept", "timeout_s")} == {
        "stage": "shutdown",
        "error_type": "TimeoutError",
        "kept": True,
        "timeout_s": 5,
    }
    assert counter.drain() == {key: 1}  # still counted, for a flush that can still run
