"""Native ingestion: idempotency, rollups, per-span rejections, limits, redaction, cost."""

import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import PAT_PREFIX, generate_key
from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from app.pricing.prices import ALL_SNAPSHOTS
from tests.helpers import (
    Workspace,
    bearer,
    new_span_id,
    new_trace_id,
    span,
)


async def _ingest(
    client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]
) -> httpx.Response:
    return await client.post("/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key))


async def _trace_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[Any, ...]]:
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            select(
                Trace.trace_id,
                Trace.name,
                Trace.environment,
                Trace.tags,
                Trace.started_at,
                Trace.ended_at,
                Trace.span_count,
                Trace.error_count,
                Trace.input_tokens,
                Trace.output_tokens,
                Trace.cost_usd,
                Trace.has_unpriced,
            ).order_by(Trace.trace_id)
        )
        return [tuple(row) for row in rows]


async def test_same_batch_twice_yields_identical_rollups(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    root_id = new_span_id()
    start = datetime.now(UTC) - timedelta(minutes=10)
    batch = [
        span(
            trace_id=trace_id,
            span_id=root_id,
            kind="chain",
            name="answer",
            model=None,
            provider=None,
            usage=None,
            start=start,
            duration_ms=900,
        ),
        span(trace_id=trace_id, parent_span_id=root_id, start=start + timedelta(milliseconds=50)),
        span(
            trace_id=trace_id,
            parent_span_id=root_id,
            status="error",
            start=start + timedelta(milliseconds=400),
            model="mystery-model-9000",
        ),
    ]

    first = await _ingest(client, workspace, batch)
    assert first.status_code == 200
    assert first.json() == {"accepted": 3, "rejected": []}
    after_first = await _trace_rows(session_factory)

    second = await _ingest(client, workspace, batch)
    assert second.json() == {"accepted": 3, "rejected": []}
    after_second = await _trace_rows(session_factory)

    assert after_first == after_second
    (row,) = after_second
    assert row[6:10] == (3, 1, 2000, 1000)  # span_count, error_count, input, output tokens
    assert row[10] is not None  # gpt-4o-mini span is priced...
    assert row[11] is True  # ...but the unknown model leaves the trace partially unpriced
    assert row[3] == ["beta"]


async def test_rollups_accumulate_across_batches_and_trace_fields_merge(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    start = datetime.now(UTC) - timedelta(minutes=10)
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=trace_id,
                start=start,
                trace={"name": "first", "environment": "prod", "tags": ["a"]},
            )
        ],
    )
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=trace_id,
                start=start + timedelta(seconds=2),
                trace={"name": None, "environment": None, "release": "v2", "tags": ["b"]},
            )
        ],
    )

    (row,) = await _trace_rows(session_factory)
    assert row[1] == "first"  # null in the later batch does not overwrite
    assert row[2] == "prod"
    assert row[3] == ["a", "b"]
    assert row[6] == 2
    assert row[4] == start
    assert row[5] == start + timedelta(seconds=2, milliseconds=100)


async def test_per_span_rejections_keep_valid_spans(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    now = datetime.now(UTC)
    duplicate_id = new_span_id()
    spans: list[Any] = [
        span(trace_id=trace_id),
        span(trace_id="not-hex"),
        span(trace_id=trace_id, start=now, end_time=(now - timedelta(seconds=1)).isoformat()),
        span(trace_id=trace_id, start=now + timedelta(hours=1)),
        span(trace_id=trace_id, start=now - timedelta(days=91)),
        span(trace_id=trace_id, usage={"input_tokens": 5, "output_tokens": 1, "cached_tokens": 9}),
        span(trace_id=trace_id, start_time="2026-01-01T00:00:00"),  # no offset
        "not an object",
        span(trace_id=trace_id, span_id=duplicate_id, name="first copy"),
        span(trace_id=trace_id, span_id=duplicate_id, name="second copy"),
        span(trace_id=trace_id, usage={"input_tokens": "12"}),
    ]
    response = await _ingest(client, workspace, spans)
    body = response.json()

    assert response.status_code == 200
    assert body["accepted"] == 2
    reasons = {item["index"]: item["reason"] for item in body["rejected"]}
    assert sorted(reasons) == [1, 2, 3, 4, 5, 6, 7, 8, 10]
    assert reasons[1].startswith("trace_id")
    assert "before start_time" in reasons[2]
    assert "future" in reasons[3]
    assert "90 days" in reasons[4]
    assert "cached_tokens" in reasons[5]
    assert "UTC offset" in reasons[6]
    assert "duplicate" in reasons[8]
    assert reasons[10].startswith("usage.input_tokens")
    assert body["rejected"][0]["span_id"] == spans[1]["span_id"]


async def test_unix_nanosecond_timestamps(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    start = datetime.now(UTC).replace(microsecond=123456) - timedelta(minutes=1)
    start_ns = int(start.timestamp()) * 1_000_000_000 + 123_456_000
    response = await _ingest(
        client,
        workspace,
        [span(trace_id=new_trace_id(), start_time=start_ns, end_time=start_ns + 250_000_000)],
    )
    assert response.json()["accepted"] == 1
    async with session_factory() as db:
        await bypass_rls(db)
        stored = (await db.execute(select(Span.started_at, Span.duration_ms))).one()
    assert stored.started_at == start
    assert stored.duration_ms == pytest.approx(250.0)


async def test_batch_over_span_limit_is_413(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    spans = [{"trace_id": trace_id}] * 1001
    response = await _ingest(client, workspace, spans)
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


async def test_body_over_5mb_is_413(client: httpx.AsyncClient, workspace: Workspace) -> None:
    huge = json.dumps({"spans": [span(trace_id=new_trace_id(), input="x" * (5 * 1024 * 1024))]})
    response = await client.post(
        "/v1/traces",
        content=huge,
        headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_gzip_bomb_is_413(client: httpx.AsyncClient, workspace: Workspace) -> None:
    body = json.dumps({"spans": [span(trace_id=new_trace_id(), input=" " * (6 * 1024 * 1024))]})
    compressed = gzip.compress(body.encode())
    assert len(compressed) < 1024 * 1024
    response = await client.post(
        "/v1/traces",
        content=compressed,
        headers={**bearer(workspace.api_key), "Content-Encoding": "gzip"},
    )
    assert response.status_code == 413


async def test_gzip_body_is_accepted(client: httpx.AsyncClient, workspace: Workspace) -> None:
    body = json.dumps({"spans": [span(trace_id=new_trace_id())]}).encode()
    response = await client.post(
        "/v1/traces",
        content=gzip.compress(body),
        headers={**bearer(workspace.api_key), "Content-Encoding": "gzip"},
    )
    assert response.json()["accepted"] == 1


async def test_invalid_json_and_envelope(client: httpx.AsyncClient, workspace: Workspace) -> None:
    headers = bearer(workspace.api_key)
    not_json = await client.post("/v1/traces", content=b"{nope", headers=headers)
    assert not_json.status_code == 400
    nan = await client.post("/v1/traces", content=b'{"spans": [NaN]}', headers=headers)
    assert nan.status_code == 400
    wrong_shape = await client.post("/v1/traces", json={"items": []}, headers=headers)
    assert wrong_shape.status_code == 422


async def test_missing_or_malformed_key_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/traces", json={"spans": []})).status_code == 401
    malformed = await client.post("/v1/traces", json={"spans": []}, headers=bearer("sk-nope"))
    assert malformed.status_code == 401


async def test_rate_limit_returns_429_with_retry_after(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    # The bucket refills on the real database clock while the requests run, so the 429 can come a
    # little after the 101st request; send until the first one.
    limited: httpx.Response | None = None
    statuses: list[int] = []
    for _ in range(1000):
        response = await _ingest(client, workspace, [])
        statuses.append(response.status_code)
        if response.status_code == 429:
            limited = response
            break
    assert limited is not None, "ingest was never rate limited"
    assert statuses[:100] == [200] * 100  # the full burst is allowed
    assert limited.json()["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) >= 1

    await asyncio.sleep(0.05)  # one token refills every 1/50 s
    assert (await _ingest(client, workspace, [])).status_code == 200


async def _stored_span(session_factory: async_sessionmaker[AsyncSession]) -> Span:
    async with session_factory() as db:
        await bypass_rls(db)
        return (await db.scalars(select(Span))).one()


async def test_secrets_are_redacted_before_storage(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "my key is sk-ant-api03-abcdefghijklmnop_QRS and card 4242 4242 4242 4242"
                ),
            },
            {"role": "user", "content": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload"},
        ]
    }
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=new_trace_id(),
                input=payload,
                attributes={"aws": "AKIAIOSFODNN7EXAMPLE"},
                status="error",
                status_message=f"failed with {workspace.api_key}",
            )
        ],
    )
    stored = await _stored_span(session_factory)
    serialized = json.dumps([stored.input, stored.attributes, stored.status_message])

    for secret in (
        "sk-ant-api03",
        "4242 4242",
        "eyJhbGci",
        "AKIAIOSFODNN7EXAMPLE",
        workspace.api_key,
    ):
        assert secret not in serialized
    assert serialized.count("[REDACTED]") == 5


async def test_a_personal_access_token_in_a_span_is_stored_redacted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Project viewers and read keys can read spans, so a stored token would be an escalation.
    token = generate_key(PAT_PREFIX).plaintext
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=new_trace_id(),
                input={"messages": [{"role": "user", "content": f"run: SPANLIGHT_TOKEN={token}"}]},
                output={"role": "assistant", "content": json.dumps({"token": token})},
                attributes={"tool_args": {"url": f"https://x.example/?token={token}"}},
                status="error",
                status_message=f"rejected {token}",
            )
        ],
    )

    stored = await _stored_span(session_factory)

    serialized = json.dumps([stored.input, stored.output, stored.attributes, stored.status_message])
    assert token not in serialized
    assert token.rsplit("_", 1)[1] not in serialized
    assert serialized.count("[REDACTED]") == 4


async def test_large_payloads_are_truncated(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _ingest(client, workspace, [span(trace_id=new_trace_id(), output={"text": "y" * 50_000})])
    stored = await _stored_span(session_factory)
    assert stored.truncated is True
    assert isinstance(stored.output, str)
    assert len(stored.output.encode()) <= 32 * 1024
    assert isinstance(stored.input, dict)  # the small input is kept intact


async def test_capture_payloads_off_drops_input_and_output(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    patch = await workspace.owner.patch(
        f"/api/v1/projects/{workspace.project_id}", json={"capture_payloads": False}
    )
    assert patch.json()["capture_payloads"] is False
    await _ingest(client, workspace, [span(trace_id=new_trace_id())])

    stored = await _stored_span(session_factory)
    assert stored.input is None and stored.output is None
    async with session_factory() as db:
        await bypass_rls(db)
        is_sql_null = await db.scalar(text("SELECT input IS NULL FROM spans"))
    assert is_sql_null is True


async def test_cost_is_computed_from_seeded_prices(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    started = datetime.now(UTC) - timedelta(minutes=5)
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=new_trace_id(),
                start=started,
                model="gpt-4o-mini-2024-07-18",
                usage={
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cached_tokens": 400_000,
                },
            )
        ],
    )
    stored = await _stored_span(session_factory)
    # 600k uncached * 0.15 + 400k cached * 0.075 + 1M output * 0.60 (per 1M tokens), the same
    # in every seeded snapshot; the version is the latest snapshot in force when the span started.
    assert str(stored.cost_usd) == "0.72000000"
    in_force = [s for s in ALL_SNAPSHOTS if s.effective_from <= started]
    assert stored.pricing_version == max(in_force, key=lambda s: s.effective_from).version


async def test_unknown_model_or_missing_usage_has_null_cost(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    await _ingest(
        client,
        workspace,
        [
            span(trace_id=trace_id, model="gpt-4.1-turbo"),
            span(trace_id=trace_id, usage={"input_tokens": 10}),
        ],
    )
    async with session_factory() as db:
        await bypass_rls(db)
        costs = (await db.scalars(select(Span.cost_usd))).all()
        trace = (await db.scalars(select(Trace))).one()
    assert costs == [None, None]
    assert trace.cost_usd is None
    assert trace.has_unpriced is True
