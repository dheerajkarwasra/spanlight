"""A span that would pass validation but fail at the database is rejected alone, never the batch.

The span upsert is one statement for the whole batch, so a value the columns cannot hold would
fail every span in it. Every such value must be refused while the span is validated, with a
reason, and every documented maximum must still be stored.
"""

import json
import sys
import uuid
from typing import Any

import httpx
import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.ingest import pipeline
from app.ingest.normalize import normalize_span
from app.ingest.schemas import MAX_TOKEN_COUNT
from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import ingest_native, stored_spans, telemetry_rows

_FOUR_BYTES = "\U0001d538"  # one character, four bytes of UTF-8


def _good() -> dict[str, Any]:
    return span(trace_id=new_trace_id())


async def _assert_only_the_middle_span_is_rejected(
    response: httpx.Response,
    session_factory: async_sessionmaker[AsyncSession],
    reason_prefix: str,
) -> None:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 2
    ((rejected),) = body["rejected"]
    assert rejected["index"] == 1
    assert rejected["reason"].startswith(reason_prefix), rejected["reason"]
    assert len(await stored_spans(session_factory)) == 2


@pytest.mark.parametrize(
    ("overrides", "reason_prefix"),
    [
        pytest.param(
            {"start_time": "0001-01-01T00:00:00+05:00"}, "start_time", id="start-before-year-1"
        ),
        pytest.param(
            {"end_time": "9999-12-31T23:59:59-05:00"}, "end_time", id="end-after-year-9999"
        ),
        pytest.param(
            {"time_to_first_token_ms": 10**400}, "time_to_first_token_ms", id="ttft-past-double"
        ),
    ],
)
async def test_a_span_that_fails_only_at_the_database_does_not_fail_the_batch(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    overrides: dict[str, Any],
    reason_prefix: str,
) -> None:
    bad = span(trace_id=new_trace_id(), **overrides)

    response = await ingest_native(client, workspace, [_good(), bad, _good()])

    await _assert_only_the_middle_span_is_rejected(response, session_factory, reason_prefix)


def _longest_span() -> dict[str, Any]:
    """A span with every text field, list and number at its documented maximum."""
    return span(
        trace_id=new_trace_id(),
        name=_FOUR_BYTES * 256,
        status="error",
        status_message=_FOUR_BYTES * 4096,
        provider=_FOUR_BYTES * 128,
        model=_FOUR_BYTES * 256,
        usage={
            "input_tokens": MAX_TOKEN_COUNT,
            "output_tokens": MAX_TOKEN_COUNT,
            "cached_tokens": MAX_TOKEN_COUNT,
        },
        time_to_first_token_ms=sys.float_info.max,
        attributes={f"a{index:03d}": index for index in range(256)},
        finish_reason=_FOUR_BYTES * 64,
        request_hash="f" * 32,
        prompt_version_id=str(uuid.uuid4()),
        trace={
            "name": _FOUR_BYTES * 256,
            "environment": _FOUR_BYTES * 64,
            "release": _FOUR_BYTES * 128,
            "user_id": _FOUR_BYTES * 256,
            "session_id": _FOUR_BYTES * 256,
            "tags": [f"{index:02d}" + _FOUR_BYTES * 62 for index in range(20)],
        },
    )


async def test_every_documented_maximum_is_stored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The most expensive seed price at the largest token counts: the largest seed-priced cost.
    priced = span(
        trace_id=new_trace_id(),
        provider="anthropic",
        model="claude-opus-4-5",
        usage={"input_tokens": MAX_TOKEN_COUNT, "output_tokens": MAX_TOKEN_COUNT},
    )

    response = await ingest_native(client, workspace, [_longest_span(), priced])

    assert response.json() == {"accepted": 2, "rejected": []}
    costs = {stored.model: stored.cost_usd for stored in await stored_spans(session_factory)}
    assert str(costs["claude-opus-4-5"]) == "60000.00000000"
    assert costs[_FOUR_BYTES * 256] is None  # an unknown model is unpriced


async def _price_override(workspace: Workspace, model: str, rate: str) -> None:
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides",
        json={
            "provider": "openai",
            "model_pattern": model,
            "input_per_mtok": rate,
            "output_per_mtok": rate,
            "effective_from": "2025-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text


def _priced(model: str, input_tokens: int, trace_id: str | None = None) -> dict[str, Any]:
    return span(
        trace_id=trace_id or new_trace_id(),
        provider="openai",
        model=model,
        usage={"input_tokens": input_tokens, "output_tokens": 0},
    )


async def test_a_cost_too_large_to_store_rejects_only_that_span(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The largest rate an override may have, over two million tokens: about $2,000,000, past
    # what `spans.cost_usd` (numeric(14, 8)) can hold.
    await _price_override(workspace, "huge-model", "999999.999999")

    response = await ingest_native(
        client, workspace, [_good(), _priced("huge-model", 2_000_000), _good()]
    )

    await _assert_only_the_middle_span_is_rejected(response, session_factory, "usage")


async def test_a_trace_total_too_large_to_store_keeps_its_spans(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Each span costs about $600,000 and fits; their trace's total, about $1,200,000, does not.
    await _price_override(workspace, "huge-model", "999999.999999")
    trace_id = new_trace_id()

    response = await ingest_native(
        client,
        workspace,
        [_priced("huge-model", 600_000, trace_id), _priced("huge-model", 600_000, trace_id)],
    )

    assert response.json() == {"accepted": 2, "rejected": []}
    spans, (trace,) = await telemetry_rows(session_factory, workspace.project_id)
    assert [stored["cost_usd"] for stored in spans] == [599999.9999994] * 2
    assert trace["span_count"] == 2
    assert trace["cost_usd"] is None  # unknown, never a wrong number


@pytest.mark.parametrize(
    ("status_code", "error_class"),
    [
        pytest.param("²", "unknown", id="superscript-digit"),
        pytest.param("9" * 5000, "unknown", id="5000-digits"),
        pytest.param("429", "rate_limit", id="string"),
        pytest.param(429.0, "rate_limit", id="float"),
        pytest.param(True, "unknown", id="bool"),
    ],
)
async def test_ingest_bad_status_attribute_does_not_fail_batch(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    status_code: Any,
    error_class: str,
) -> None:
    failed = span(
        trace_id=new_trace_id(), status="error", attributes={"http.status_code": status_code}
    )

    response = await ingest_native(client, workspace, [_good(), failed])

    assert response.json() == {"accepted": 2, "rejected": []}
    classes = {stored.span_id: stored.error_class for stored in await stored_spans(session_factory)}
    assert classes[failed["span_id"]] == error_class


def _normalization_failures(error_type: str) -> float:
    sample = REGISTRY.get_sample_value(
        "spanlight_span_normalization_failures_total", {"error_type": error_type}
    )
    return sample or 0.0


async def test_an_unexpected_error_while_normalizing_rejects_only_that_span(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = normalize_span

    def normalize(validated: Any, **kwargs: Any) -> Any:
        if validated.name == "boom":
            raise RuntimeError("a bug that quotes the payload: sk-secret-prompt")
        return original(validated, **kwargs)

    monkeypatch.setattr(pipeline, "normalize_span", normalize)
    boom = span(trace_id=new_trace_id(), name="boom")

    before = _normalization_failures("RuntimeError")

    with capture_logs() as logs:
        response = await ingest_native(client, workspace, [_good(), boom, _good()])

    await _assert_only_the_middle_span_is_rejected(response, session_factory, "span:")
    (logged,) = [entry for entry in logs if entry["event"] == "span_normalization_failed"]
    assert logged["index"] == 1 and logged["error_type"] == "RuntimeError"
    assert logged["origin"].startswith(f"{__name__}:")  # where the bug raised: this test
    assert _normalization_failures("RuntimeError") == before + 1
    assert "sk-secret-prompt" not in repr(logs)


@pytest.mark.parametrize(
    "number",
    [
        # Valid JSON, but Python converts at most 4300 digits.
        pytest.param("9" * 5000, id="5000-digits"),
        # Valid JSON, but past a double: it would parse as an infinity, which jsonb refuses.
        pytest.param("1e999", id="past-a-double"),
        pytest.param("-1e400", id="past-a-negative-double"),
    ],
)
async def test_a_number_python_or_jsonb_cannot_hold_is_invalid_json(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    number: str,
) -> None:
    document = {"spans": [span(trace_id=new_trace_id(), attributes={"n": "NUMBER"})]}
    body = json.dumps(document).replace('"NUMBER"', number)

    response = await client.post(
        "/v1/traces",
        content=body,
        headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_JSON"
    assert await stored_spans(session_factory) == []
