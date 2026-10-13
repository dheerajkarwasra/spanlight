"""A span the gateway recorded belongs to its key: native ingestion never rewrites it."""

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.pipeline import IngestTarget, ingest_spans
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, fixture_text, read_gateway_span
from tests.helpers import span
from tests.ingest.support import ingest_native, telemetry_rows, workspace_for_project

CHAT_BODY = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Hello"}]}
TOTALS = (
    "span_count",
    "error_count",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "has_unpriced",
    "started_at",
    "ended_at",
)


def _totals(trace: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(trace[name] for name in TOTALS)


def _same_ids_other_numbers(gateway_span: dict[str, Any]) -> dict[str, Any]:
    return span(
        trace_id=gateway_span["trace_id"],
        span_id=gateway_span["span_id"],
        model="gpt-4o-mini",
        usage={"input_tokens": 5000, "output_tokens": 4000, "cached_tokens": 0},
    )


async def test_native_replay_never_overwrites_a_gateway_owned_span(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    called = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)
    assert called.status_code == 200, called.text
    project_id = gateway_workspace.project_id
    recorded = await read_gateway_span(session_factory, project_id)
    assert recorded["source_key_id"] == gateway_workspace.gateway_key_id
    # The usage in `openai/chat.json`: 19 input tokens (8 cached) and 9 output tokens.
    assert (recorded["input_tokens"], recorded["output_tokens"]) == (19, 9)
    [totals_before] = [
        _totals(t) for t in (await telemetry_rows(session_factory, str(project_id)))[1]
    ]

    native = await workspace_for_project(
        gateway_workspace.owner, str(gateway_workspace.org_id), str(project_id)
    )
    replayed = await ingest_native(gateway_client, native, [_same_ids_other_numbers(recorded)])

    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == {"accepted": 1, "rejected": []}  # counted, then left as it was
    assert await read_gateway_span(session_factory, project_id) == recorded
    [trace] = (await telemetry_rows(session_factory, str(project_id)))[1]
    assert _totals(trace) == totals_before


async def test_a_gateway_replay_of_a_gateway_span_does_overwrite(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)
    project_id = gateway_workspace.project_id
    recorded = await read_gateway_span(session_factory, project_id)
    target = IngestTarget(
        project_id=project_id,
        capture_payloads=True,
        org_id=gateway_workspace.org_id,
        source_key_id=gateway_workspace.gateway_key_id,
    )

    async with session_factory() as db, db.begin():
        outcome = await ingest_spans(db, target, [_same_ids_other_numbers(recorded)])

    assert outcome.accepted == 1
    replaced = await read_gateway_span(session_factory, project_id)
    assert (replaced["input_tokens"], replaced["output_tokens"]) == (5000, 4000)
    assert replaced["source_key_id"] == gateway_workspace.gateway_key_id
    [trace] = (await telemetry_rows(session_factory, str(project_id)))[1]
    assert (trace["span_count"], trace["input_tokens"], trace["output_tokens"]) == (1, 5000, 4000)
