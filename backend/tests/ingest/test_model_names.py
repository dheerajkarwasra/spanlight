"""Model names at ingest: surrounding whitespace is stripped, case is kept and priced exactly.

Owner decision 2026-10-13 (full test pass finding t5-05): pricing used to lower-case and strip
the model before matching while alert, rollup and budget filters compared the stored name as
is, so `GPT-4o` was priced but escaped a budget on `gpt-4o`. Now every span path (native, OTLP,
gateway) strips the name once in `SpanIn`, stores that, and pricing matches it case-sensitively:
an odd-cased model costs null ("—") instead of being counted by one rule and missed by another.
"""

import time
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from app.ingest.schemas import SpanIn
from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import ingest_native, otlp_json_document, otlp_json_span


@pytest.mark.parametrize(
    ("sent", "kept"),
    [(" gpt-4o ", "gpt-4o"), ("\tGPT-4o\n", "GPT-4o"), ("   ", None), ("gpt-4o", "gpt-4o")],
)
def test_span_model_is_stripped_but_keeps_its_case(sent: str, kept: str | None) -> None:
    validated = SpanIn.model_validate(span(trace_id=new_trace_id(), model=sent))
    assert validated.model == kept


async def _stored(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[list[tuple[Any, ...]], Trace]:
    async with session_factory() as db:
        await bypass_rls(db)
        spans = (await db.execute(select(Span.model, Span.cost_usd).order_by(Span.model))).all()
        trace = (await db.scalars(select(Trace))).one()
    return [tuple(row) for row in spans], trace


async def test_native_span_with_padded_model_is_stored_stripped_and_priced(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await ingest_native(
        client, workspace, [span(trace_id=new_trace_id(), model=" gpt-4o-mini\t")]
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1

    spans, trace = await _stored(session_factory)
    assert [model for model, _ in spans] == ["gpt-4o-mini"]
    assert spans[0][1] is not None
    assert trace.has_unpriced is False


async def test_native_span_with_odd_cased_model_keeps_it_and_costs_null(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await ingest_native(
        client, workspace, [span(trace_id=new_trace_id(), model="GPT-4o-mini")]
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1

    spans, trace = await _stored(session_factory)
    assert spans == [("GPT-4o-mini", None)]
    assert trace.cost_usd is None and trace.has_unpriced is True


async def test_otlp_span_with_padded_model_is_stored_stripped_and_priced(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    otlp_span = otlp_json_span({"gen_ai.system": "openai", "gen_ai.response.model": " gpt-4o "})
    otlp_span["attributes"] += [
        {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1000"}},
        {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "10"}},
    ]
    otlp_span["startTimeUnixNano"] = str(time.time_ns() - 60_000_000_000)
    response = await client.post(
        "/v1/otlp/traces", json=otlp_json_document(otlp_span), headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json() == {}

    spans, _ = await _stored(session_factory)
    assert [model for model, _ in spans] == ["gpt-4o"]
    assert spans[0][1] is not None
