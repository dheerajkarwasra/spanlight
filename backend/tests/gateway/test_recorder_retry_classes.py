"""Which failed span writes the recorder retries, and what it logs about a lost one.

Only a failure a later try may not meet is retried: a lost connection, a timeout, an
operational error such as a deadlock. A value or constraint the database refuses would be
refused again, so that span is counted lost on its first try instead of after about 2.6 s of
retries. A lost span is logged by the error's type and SQLSTATE, never by its message, which
(with a Postgres `DETAIL`) may quote the payload.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import Any

import psycopg
import pytest
from sqlalchemy import exc

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.gateway import recorder, span_write
from app.gateway.execute import execute
from tests.gateway.support import chat_request
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import (
    FakeProvider,
    drain_recorder,
    fixture_text,
    logged,
    metric_delta,
)

SECRET = "sk-test-" + "b" * 32  # stands in for payload text a database message could quote
STATEMENT = "INSERT INTO spans ..."


@dataclass
class RefusedWrites:
    """Every span write fails with `error`; `writes` counts the tries, `waits` the backoff."""

    error: Exception
    writes: int = 0
    waits: list[float] = field(default_factory=list)

    async def ingest(self, *_: Any, **__: Any) -> Any:
        self.writes += 1
        raise self.error

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def _database_error(wrapper: type[exc.DBAPIError], sqlstate: str) -> exc.DBAPIError:
    """`wrapper` around the psycopg error of `sqlstate`, its message quoting `SECRET`."""
    orig = psycopg.errors.lookup(sqlstate)(f"value refused: {SECRET}")
    return wrapper(STATEMENT, None, orig)


async def _call_with(
    error: Exception,
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> RefusedWrites:
    """One answered call whose span writes all fail with `error`, the recorder drained."""
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    refused = RefusedWrites(error)
    monkeypatch.setattr(span_write, "ingest_spans", refused.ingest)
    context = await gateway_context(keyed=False)
    result = await execute(
        chat_request(stream=False), dataclasses.replace(context, sleep=refused.sleep)
    )
    await drain_recorder()
    assert result.status == 200  # the client never waits on its span
    return refused


@pytest.mark.parametrize(
    ("wrapper", "sqlstate"),
    [
        (exc.DataError, "22P05"),  # untranslatable character: a NUL in jsonb
        (exc.IntegrityError, "23505"),  # unique violation
        (exc.ProgrammingError, "42703"),  # undefined column: a bug
    ],
    ids=["data_error", "integrity_error", "programming_error"],
)
async def test_a_write_the_database_refuses_is_lost_on_its_first_try(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    wrapper: type[exc.DBAPIError],
    sqlstate: str,
) -> None:
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    lost = recorder.spans_lost()

    refused = await _call_with(
        _database_error(wrapper, sqlstate), gateway_context, fake_provider, monkeypatch
    )

    assert (refused.writes, refused.waits) == (1, [])
    assert errors.value == 1
    assert recorder.spans_lost() == lost + 1
    assert logged(caplog, "gateway.record_retry") == []
    [entry] = logged(caplog, "gateway.record_failed")
    assert (entry["error_type"], entry["sqlstate"], entry["tries"]) == (
        wrapper.__name__,
        sqlstate,
        1,
    )
    assert entry["span_id"] and entry["trace_id"]
    assert SECRET not in caplog.text  # neither the message nor its DETAIL reaches the log


async def test_a_transient_database_error_is_retried_then_logged_by_type(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    error = _database_error(exc.OperationalError, "40P01")  # deadlock detected

    refused = await _call_with(error, gateway_context, fake_provider, monkeypatch)

    delays = list(recorder.RECORD_RETRY_DELAYS_S)
    assert (refused.writes, refused.waits) == (len(delays) + 1, delays)
    assert errors.value == 1
    [entry] = logged(caplog, "gateway.record_failed")
    assert (entry["error_type"], entry["sqlstate"]) == ("OperationalError", "40P01")
    assert entry["tries"] == len(delays) + 1
    assert SECRET not in caplog.text
