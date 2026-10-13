"""OTLP values that do not fit a native column drop that value, never the span.

Owner decision 2026-10-13 (full test pass finding t5-09): a service whose
`deployment.environment` is over 64 characters, or whose `service.version` is over 128, lost
every span it exported, as did an exporter that writes token counts as doubles. Now the
offending trace field is dropped, an integral double token count becomes an integer, a
non-integral or non-finite one is dropped, and one warning per batch counts what was dropped
without quoting it.
"""

import time
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from tests.gateway_support import logged
from tests.helpers import Workspace, bearer
from tests.ingest.support import otlp_json_span

LONG_ENVIRONMENT = "e" * 65
LONG_RELEASE = "v" * 129


def _document(resource: dict[str, str], *spans: dict[str, Any]) -> dict[str, Any]:
    attributes = [{"key": key, "value": {"stringValue": value}} for key, value in resource.items()]
    return {
        "resourceSpans": [
            {"resource": {"attributes": attributes}, "scopeSpans": [{"spans": list(spans)}]}
        ]
    }


def _llm_span(**usage: dict[str, Any]) -> dict[str, Any]:
    span = otlp_json_span({"gen_ai.system": "openai", "gen_ai.response.model": "gpt-4o"})
    span["startTimeUnixNano"] = str(time.time_ns() - 60_000_000_000)
    span["attributes"] += [
        {"key": f"gen_ai.usage.{name}", "value": value} for name, value in usage.items()
    ]
    return span


async def _post(
    client: httpx.AsyncClient, workspace: Workspace, document: dict[str, Any]
) -> httpx.Response:
    return await client.post("/v1/otlp/traces", json=document, headers=bearer(workspace.api_key))


async def _stored(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[list[Span], list[Trace]]:
    async with session_factory() as db:
        await bypass_rls(db)
        spans = list((await db.scalars(select(Span))).all())
        traces = list((await db.scalars(select(Trace))).all())
    return spans, traces


async def test_over_long_environment_and_release_are_dropped_and_the_span_kept(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    resource = {
        "service.name": "checkout",
        "deployment.environment": LONG_ENVIRONMENT,
        "service.version": LONG_RELEASE,
    }
    response = await _post(
        client, workspace, _document(resource, otlp_json_span(), otlp_json_span())
    )

    assert response.status_code == 200 and response.json() == {}
    spans, traces = await _stored(session_factory)
    assert len(spans) == 2
    assert all(trace.environment is None and trace.release is None for trace in traces)
    (warning,) = logged(caplog, "otlp_values_dropped")
    assert [record.levelname for record in caplog.records if record.msg is warning] == ["WARNING"]
    assert warning["environment"] == 2 and warning["release"] == 2
    assert warning["token_counts"] == 0
    assert LONG_ENVIRONMENT not in repr(warning) and LONG_RELEASE not in repr(warning)


async def test_values_that_fit_are_kept_and_nothing_is_logged(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    resource = {"deployment.environment": "e" * 64, "service.version": "v" * 128}
    response = await _post(client, workspace, _document(resource, otlp_json_span()))

    assert response.status_code == 200 and response.json() == {}
    _, [trace] = await _stored(session_factory)
    assert trace.environment == "e" * 64 and trace.release == "v" * 128
    assert logged(caplog, "otlp_values_dropped") == []


async def test_integral_double_token_counts_become_integers(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    span = _llm_span(
        input_tokens={"doubleValue": 12.0},
        output_tokens={"doubleValue": 3},
        cache_read_input_tokens={"doubleValue": "4.0"},
    )
    response = await _post(client, workspace, _document({}, span))

    assert response.status_code == 200 and response.json() == {}
    [stored], _ = await _stored(session_factory)
    assert (stored.input_tokens, stored.output_tokens, stored.cached_tokens) == (12, 3, 4)
    assert stored.cost_usd is not None
    assert logged(caplog, "otlp_values_dropped") == []


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"doubleValue": 12.5}, id="fraction"),
        pytest.param({"doubleValue": "NaN"}, id="nan"),
        pytest.param({"doubleValue": "Infinity"}, id="infinity"),
        pytest.param({"doubleValue": "-Infinity"}, id="minus-infinity"),
        # Decoding cannot tell this string from a non-finite double, so it is dropped the same way.
        pytest.param({"stringValue": "NaN"}, id="nan-string"),
    ],
)
async def test_a_token_count_that_is_not_a_whole_number_is_dropped_and_the_span_kept(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
    value: dict[str, Any],
) -> None:
    span = _llm_span(input_tokens=value, output_tokens={"intValue": "7"})
    response = await _post(client, workspace, _document({}, span))

    assert response.status_code == 200 and response.json() == {}
    [stored], _ = await _stored(session_factory)
    assert stored.input_tokens is None and stored.output_tokens == 7
    assert stored.cost_usd is None  # unknown usage is never priced as zero
    (warning,) = logged(caplog, "otlp_values_dropped")
    assert warning["token_counts"] == 1
    assert warning["environment"] == 0 and warning["release"] == 0
