"""Per-project ingest caps on `POST /v1/traces` and `POST /v1/otlp/traces` (spec C5).

Every test goes through the real routes with the real key dependency and binds nothing itself:
the usage row and the replay probe are only visible under the binding the route takes, so a
route that read them too early would admit what these tests expect refused (reviewer C1/N1).
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from sqlalchemy import Engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import ingest as ingest_api
from app.core.observability import INGEST_QUOTA_REPLAYS_ADMITTED
from app.db.rls import bind_project, bypass_rls
from app.gateway.span_losses import loss_counter
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.ingest.quota import next_midnight, seconds_until
from app.rollups.compute import floor_hour
from app.telemetry_losses.flush import flush_once
from tests.conftest import _browsers, _make_client, _running
from tests.gateway_support import metric_delta
from tests.helpers import Workspace, bearer, create_workspace, new_span_id, new_trace_id, span
from tests.ingest.faults import delete_project
from tests.ingest.support import (
    ingest_native,
    lift_project_concurrency,
    otlp_json_document,
    otlp_json_span,
)

Sessions = async_sessionmaker[AsyncSession]
PROBE = "ingest quota replay probe"


def _batch(count: int) -> list[dict[str, Any]]:
    trace_id = new_trace_id()
    return [span(trace_id=trace_id) for _ in range(count)]


def _uppercased(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**s, "trace_id": s["trace_id"].upper(), "span_id": s["span_id"].upper()} for s in batch
    ]


def _body_bytes(batch: list[dict[str, Any]]) -> int:
    return len(httpx.Request("POST", "http://x", json={"spans": batch}).content)


async def _set_caps(sessions: Sessions, project_id: str, **caps: int | None) -> None:
    assignments = ", ".join(f"ingest_{name} = :{name}" for name in caps)
    async with sessions() as db, db.begin():
        await db.execute(
            text(f"UPDATE projects SET {assignments} WHERE id = :id"), {"id": project_id, **caps}
        )


async def _usage(sessions: Sessions, project_id: str) -> list[tuple[str, int, int]]:
    async with sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT day, spans, bytes FROM ingest_quota_days WHERE project_id = :p ORDER BY day"
            ),
            {"p": project_id},
        )
        return [(str(row.day), row.spans, row.bytes) for row in rows]


async def _losses(sessions: Sessions, project_id: str) -> dict[tuple[str, str], int]:
    await flush_once(sessions, loss_counter())
    async with sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT source, reason, sum(count) FROM telemetry_losses_hourly "
                "WHERE project_id = :p GROUP BY source, reason"
            ),
            {"p": project_id},
        )
        return {(row[0], row[1]): int(row[2]) for row in rows}


async def _bucket_tokens(sessions: Sessions, key: str) -> float | None:
    async with sessions() as db:
        return await db.scalar(
            text("SELECT tokens FROM rate_limit_buckets WHERE key = :key"), {"key": key}
        )


def _pin(app: Any, moment: datetime) -> None:
    app.state.clock = lambda: moment


def _now() -> datetime:
    """The wall clock at whole seconds, pinned on the app so Retry-After and resets_at are exact."""
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_daily_span_quota_admits_the_crossing_batch_and_refuses_the_next(
    app: Any, client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    now = _now()
    _pin(app, now)
    await _set_caps(session_factory, workspace.project_id, spans_per_day=10)
    first = _batch(10)

    admitted = await ingest_native(client, workspace, first)
    refused = await ingest_native(client, workspace, _batch(1))

    assert admitted.status_code == 200 and admitted.json()["accepted"] == 10
    assert refused.status_code == 429, refused.text
    assert refused.headers["content-type"] == "application/problem+json"
    assert refused.headers["retry-after"] == str(seconds_until(next_midnight(now), now))
    body = refused.json()
    assert body["code"] == "INGEST_QUOTA_EXCEEDED"
    assert body["detail"] == (
        "This project's daily ingest quota of 10 spans is used up; it resets at 00:00 UTC."
    )
    assert (body["quota"], body["limit"], body["used"]) == ("spans_per_day", 10, 10)
    assert body["resets_at"] == next_midnight(now).isoformat().replace("+00:00", "Z")
    assert await _usage(session_factory, workspace.project_id) == [
        (str(now.date()), 10, _body_bytes(first))
    ]
    assert await _losses(session_factory, workspace.project_id) == {("ingest", "quota_spans"): 1}

    completeness = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/telemetry-completeness"
    )
    assert {"source": "ingest", "reason": "quota_spans", "count": 1} in completeness.json()[
        "losses"
    ]
    quota = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/ingest-quota")
    assert quota.status_code == 200, quota.text
    assert quota.json()["today"] == {
        "day": str(now.date()),
        "spans": 10,
        "bytes": _body_bytes(first),
        "resets_at": body["resets_at"],
    }
    assert quota.json()["rejected_days"] == [{"day": str(now.date()), "spans": 1, "rate_spans": 0}]
    assert quota.json()["limits"]["spans_per_day"] == {"value": 10, "source": "project"}
    assert quota.json()["limits"]["bytes_per_day"] == {
        "value": 5 * 1024**3,
        "source": "default",
    }


async def test_daily_byte_quota_refuses_with_its_own_members(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    await _set_caps(session_factory, workspace.project_id, bytes_per_day=1)
    first = _batch(1)

    assert (await ingest_native(client, workspace, first)).status_code == 200
    refused = await ingest_native(client, workspace, _batch(1))

    assert refused.status_code == 429
    body = refused.json()
    assert body["code"] == "INGEST_QUOTA_EXCEEDED"
    assert (body["quota"], body["limit"], body["used"]) == (
        "bytes_per_day",
        1,
        _body_bytes(first),
    )
    assert "of 1 bytes is used up" in body["detail"]
    assert await _losses(session_factory, workspace.project_id) == {("ingest", "quota_bytes"): 1}


async def test_the_usage_read_runs_under_the_project_binding(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    """A day row written by another session is what the route reads (reviewer N1)."""
    await _set_caps(session_factory, workspace.project_id, spans_per_day=7)
    async with session_factory() as db, db.begin():
        await bind_project(db, uuid.UUID(workspace.project_id))
        await db.execute(
            text(
                "INSERT INTO ingest_quota_days (project_id, day, spans, bytes) "
                "VALUES (:p, CAST(now() AT TIME ZONE 'UTC' AS date), 7, 1)"
            ),
            {"p": workspace.project_id},
        )

    refused = await ingest_native(client, workspace, _batch(1))

    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "INGEST_QUOTA_EXCEEDED"
    assert refused.json()["used"] == 7


async def test_replays_count_nothing_and_a_partial_replay_counts_its_whole_body(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    batch = _batch(3)
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    first = await _usage(session_factory, workspace.project_id)

    replayed = await ingest_native(client, workspace, batch)
    assert replayed.status_code == 200 and replayed.json()["accepted"] == 3
    assert await _usage(session_factory, workspace.project_id) == first

    extended = [*batch, span(trace_id=batch[0]["trace_id"])]
    assert (await ingest_native(client, workspace, extended)).status_code == 200
    [(_, spans, byte_count)] = await _usage(session_factory, workspace.project_id)
    assert spans == 4
    assert byte_count == first[0][2] + _body_bytes(extended)


async def test_replay_at_the_cap_is_admitted_new_data_is_refused(
    app: Any,
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: Sessions,
    statements: list[str],
) -> None:
    now = _now()
    _pin(app, now)
    await _set_caps(session_factory, workspace.project_id, spans_per_day=10, spans_per_second=1)
    batch = _batch(10)
    bucket = f"ingest:project:{workspace.project_id}"

    assert (await ingest_native(client, workspace, batch)).status_code == 200
    assert not [s for s in statements if PROBE in s]  # under the cap: no probe
    after_first = await _usage(session_factory, workspace.project_id)
    tokens_after_first = await _bucket_tokens(session_factory, bucket)
    assert tokens_after_first is not None and tokens_after_first <= 4990

    replays = metric_delta(INGEST_QUOTA_REPLAYS_ADMITTED)
    replayed = await ingest_native(client, workspace, _uppercased(batch))
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["accepted"] == 10
    assert await _usage(session_factory, workspace.project_id) == after_first
    assert replays.value == 1
    tokens_after_replay = await _bucket_tokens(session_factory, bucket)
    assert tokens_after_replay is not None and tokens_after_replay <= tokens_after_first - 9

    refused = await ingest_native(client, workspace, [*batch, span(trace_id=batch[0]["trace_id"])])
    assert refused.status_code == 429 and refused.json()["code"] == "INGEST_QUOTA_EXCEEDED"
    tokens_after_refusal = await _bucket_tokens(session_factory, bucket)
    assert tokens_after_refusal is not None and tokens_after_refusal >= tokens_after_replay

    zero_id = span(trace_id=batch[0]["trace_id"], span_id="0" * 16)
    refused_zero = await ingest_native(client, workspace, [*batch, zero_id])
    assert (
        refused_zero.status_code == 429 and refused_zero.json()["code"] == "INGEST_QUOTA_EXCEEDED"
    )

    assert await _losses(session_factory, workspace.project_id) == {("ingest", "quota_spans"): 22}
    assert len([s for s in statements if PROBE in s]) == 3


async def test_project_span_rate_refuses_the_sixth_full_batch(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    await _set_caps(session_factory, workspace.project_id, spans_per_second=1)

    for round_ in range(5):
        response = await ingest_native(client, workspace, _batch(1000))
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == 1000
        [(_, spans, _)] = await _usage(session_factory, workspace.project_id)
        assert spans == 1000 * (round_ + 1)

    refused = await ingest_native(client, workspace, _batch(1000))

    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "RATE_LIMITED"
    assert refused.json()["detail"] == "Ingestion rate limit exceeded for this project."
    assert int(refused.headers["retry-after"]) >= 1
    [(_, spans, _)] = await _usage(session_factory, workspace.project_id)
    assert spans == 5000
    assert await _losses(session_factory, workspace.project_id) == {("ingest", "quota_rate"): 1000}
    # The key's own bucket paid one request token per call and nothing for the refusal.
    async with session_factory() as db:
        key_tokens = await db.scalar(
            text("SELECT tokens FROM rate_limit_buckets WHERE key LIKE 'ingest:key:%'")
        )
    assert key_tokens is not None and key_tokens >= 100 - 6


async def test_an_admitted_otlp_batch_counts_toward_the_same_day_row(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    assert (await ingest_native(client, workspace, _batch(1))).status_code == 200
    response = await client.post(
        "/v1/otlp/traces",
        json=otlp_json_document(otlp_json_span()),
        headers=bearer(workspace.api_key),
    )
    assert response.status_code == 200, response.text

    [(_, spans, _)] = await _usage(session_factory, workspace.project_id)
    assert spans == 2


async def test_otlp_refusals_are_problem_json_in_both_encodings(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    await _set_caps(session_factory, workspace.project_id, spans_per_day=1)
    assert (await ingest_native(client, workspace, _batch(1))).status_code == 200
    headers = bearer(workspace.api_key)

    as_json = await client.post(
        "/v1/otlp/traces", json=otlp_json_document(otlp_json_span()), headers=headers
    )
    request = ExportTraceServiceRequest()
    scope = request.resource_spans.add().scope_spans.add()
    scope.spans.append(
        ProtoSpan(
            trace_id=bytes.fromhex(new_trace_id()),
            span_id=bytes.fromhex(new_span_id()),
            name="call",
            start_time_unix_nano=1_700_000_000_000_000_000,
            end_time_unix_nano=1_700_000_000_000_000_000,
        )
    )
    as_protobuf = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**headers, "Content-Type": "application/x-protobuf"},
    )

    for response in (as_json, as_protobuf):
        assert response.status_code == 429, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert int(response.headers["retry-after"]) >= 1
        assert response.json()["code"] == "INGEST_QUOTA_EXCEEDED"
        assert "partialSuccess" not in response.json()
    assert await _losses(session_factory, workspace.project_id) == {("ingest", "quota_spans"): 2}


async def test_the_gateway_recorder_path_is_neither_checked_nor_counted(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    await _set_caps(session_factory, workspace.project_id, spans_per_day=1)
    first = _batch(1)
    assert (await ingest_native(client, workspace, first)).status_code == 200
    assert (await ingest_native(client, workspace, _batch(1))).status_code == 429
    gateway_span = span(trace_id=new_trace_id())
    target = IngestTarget(
        project_id=uuid.UUID(workspace.project_id),
        capture_payloads=True,
        org_id=uuid.UUID(workspace.org_id),
        source_key_id=uuid.uuid4(),
    )

    async with session_factory() as db:
        outcome = await ingest_spans(db, target, [gateway_span])
        await db.commit()
    assert outcome.accepted == 1 and outcome.inserted == 1
    assert await _usage(session_factory, workspace.project_id) == [
        (str(datetime.now(UTC).date()), 1, _body_bytes(first))
    ]
    # A native replay of the gateway's span is left alone: accepted, inserted nothing.
    await _set_caps(session_factory, workspace.project_id, spans_per_day=None)
    replayed = await ingest_native(client, workspace, [gateway_span])
    assert replayed.status_code == 200 and replayed.json()["accepted"] == 1
    [(_, spans, _)] = await _usage(session_factory, workspace.project_id)
    assert spans == 1


@pytest.mark.parametrize("otlp", [False, True], ids=["native", "otlp"])
async def test_a_project_deleted_after_the_key_check_is_401(
    client: httpx.AsyncClient,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    otlp: bool,
) -> None:
    original = ingest_api.bind_and_lock_project

    async def delete_first(*args: Any, **kwargs: Any) -> Any:
        await delete_project(workspace.project_id)
        return await original(*args, **kwargs)

    monkeypatch.setattr(ingest_api, "bind_and_lock_project", delete_first)
    headers = bearer(workspace.api_key)

    if otlp:
        response = await client.post(
            "/v1/otlp/traces", json=otlp_json_document(otlp_json_span()), headers=headers
        )
    else:
        response = await client.post("/v1/traces", json={"spans": _batch(1)}, headers=headers)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Missing, invalid or revoked API key."


async def test_the_day_rolls_over_on_the_app_clock(
    app: Any, client: httpx.AsyncClient, workspace: Workspace, session_factory: Sessions
) -> None:
    today = datetime.now(UTC).date()
    last_second = datetime.combine(today, time(23, 59, 59), UTC)
    first_second = datetime.combine(today + timedelta(days=1), time(0, 0, 1), UTC)
    await _set_caps(session_factory, workspace.project_id, spans_per_day=1)
    _pin(app, last_second)
    assert (await ingest_native(client, workspace, _batch(1))).status_code == 200

    refused = await ingest_native(client, workspace, _batch(1))
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "1"
    assert refused.json()["resets_at"] == f"{today + timedelta(days=1)}T00:00:00Z"

    _pin(app, first_second)
    admitted = await ingest_native(client, workspace, _batch(1))
    assert admitted.status_code == 200, admitted.text
    assert [day for day, *_ in await _usage(session_factory, workspace.project_id)] == [
        str(today),
        str(today + timedelta(days=1)),
    ]
    await flush_once(session_factory, loss_counter())
    async with session_factory() as db:
        await bypass_rls(db)
        hours = (
            await db.scalars(
                text(
                    "SELECT bucket_start FROM telemetry_losses_hourly "
                    "WHERE project_id = :p AND reason = 'quota_spans'"
                ),
                {"p": workspace.project_id},
            )
        ).all()
    assert hours == [floor_hour(last_second)]


async def test_unlimited_settings_refuse_nothing_and_still_count(
    settings: Any, session_factory: Sessions
) -> None:
    unlimited = settings.model_copy(
        update={"ingest_spans_per_second": 0, "ingest_spans_per_day": 0, "ingest_bytes_per_day": 0}
    )
    async with (
        _running(unlimited) as app,
        _browsers(app) as browsers,
        _make_client(app) as client,
    ):
        workspace = await create_workspace(await browsers("owner@example.com"))
        for _ in range(3):
            assert (await ingest_native(client, workspace, _batch(2))).status_code == 200

        [(_, spans, _)] = await _usage(session_factory, workspace.project_id)
        assert spans == 6
        quota = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/ingest-quota")
        assert quota.status_code == 200, quota.text
        assert quota.json()["limits"] == {
            "spans_per_second": {"value": None, "source": "default"},
            "spans_per_day": {"value": None, "source": "default"},
            "bytes_per_day": {"value": None, "source": "default"},
        }
        assert quota.json()["rejected_days"] == []


async def test_concurrent_batches_at_the_cap_overshoot_by_at_most_the_batches_in_flight(
    app: Any,
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lift_project_concurrency(app, monkeypatch)
    await _set_caps(session_factory, workspace.project_id, spans_per_day=500)

    responses = await asyncio.gather(
        *(ingest_native(client, workspace, _batch(50)) for _ in range(20))
    )

    statuses = [response.status_code for response in responses]
    assert set(statuses) <= {200, 429}, statuses
    assert all(
        response.json()["code"] == "INGEST_QUOTA_EXCEEDED"
        for response in responses
        if response.status_code == 429
    )
    admitted = sum(
        response.json()["accepted"] for response in responses if response.status_code == 200
    )
    [(_, spans, _)] = await _usage(session_factory, workspace.project_id)
    assert spans == admitted
    assert 500 <= admitted <= 500 + 19 * 50
