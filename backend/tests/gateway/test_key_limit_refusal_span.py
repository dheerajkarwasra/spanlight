"""A key-limit refusal (`RATE_LIMITED`, answered by the router before `execute`) leaves a span.

The span is the platform owner's proof that "the 429s" were the gateway key's own limit, not the
provider's: class `rate_limit`, kind `gateway`, under the key's
project. It is queued on the recorder's bounded backlog at low priority, so the refusal gains no
database round trip: once half the backlog is taken it is dropped as an `overflow` loss, which
leaves admitted calls the other half, and the client still gets its 429, byte for byte the answer
it got before refusals were recorded. The refusal does not count in the key's minute. A request
whose key never authenticated has no project, so it records nothing. Each refusal span starts a
trace of its own, whatever trace the client pinned, so a flood can never grow one trace. (How
many refusals get a span is `test_refusal_sampling.py`; here every refusal is let through unless
a test says otherwise.)
"""

import asyncio
import dataclasses
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.observability import GATEWAY_RECORD_FAILURES
from app.core.security import GATEWAY_KEY_PREFIX, parse_key
from app.db.models import Project
from app.db.rls import bind_project
from app.gateway import recorder, refusal_span, span_losses, span_write
from app.gateway.context import GatewayRequest
from app.gateway.errors import rate_limited, render_error
from app.gateway.execute import execute
from app.gateway.limits import key_bucket_key, org_bucket_key, per_minute_bucket
from app.gateway.refusal_sampler import RefusalSampler
from app.gateway.refusal_span import record_refusal
from app.gateway.runtime import GatewayRuntime
from app.ingest import pipeline
from app.telemetry_losses.counter import LossCounter
from tests.gateway.support import CHAT_BODY, MESSAGES_BODY, chat_request, key_minute_counts
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import (
    FakeProvider,
    drain_recorder,
    fixture_text,
    metric_delta,
    read_gateway_span,
    read_gateway_spans,
)
from tests.rls.factories import create_project

CALL_PATH = "/gw/v1/chat/completions"
BACKLOG = 4  # refusals may take 2 places, admitted calls always keep the other 2
# The headers of a refusal: the gateway's error headers and what the JSON response adds. No
# `X-Spanlight-Attempts` or `X-Spanlight-Cache`: the call never reached `execute`.
REFUSAL_HEADERS = {
    "content-length",
    "content-type",
    "retry-after",
    "x-request-id",
    "x-spanlight-code",
}

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
def gateway_settings(gateway_settings: Settings) -> Settings:
    """One span written at a time and at most `BACKLOG` waiting or running."""
    return gateway_settings.model_copy(
        update={"gateway_record_backlog": BACKLOG, "gateway_record_concurrency": 1}
    )


@pytest.fixture(autouse=True)
async def own_recorder(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Writers built from this module's settings, and a loss counter of this test's own."""
    monkeypatch.setattr(recorder, "_writers", None)
    monkeypatch.setattr(span_losses, "_counter", LossCounter())
    monkeypatch.setattr(refusal_span, "_sampler", RefusalSampler(interval_s=0.0))
    yield
    await drain_recorder()


def _runtime(app: Any) -> GatewayRuntime:
    runtime = app.state.gateway_runtime
    assert isinstance(runtime, GatewayRuntime)
    return runtime


async def _limit_key(app: Any, workspace: GatewayWorkspace) -> None:
    """Give the key a limit of one request a minute, and spend that one request."""
    edited = await workspace.owner.patch(
        f"/api/v1/projects/{workspace.project_id}/gateway/keys/{workspace.gateway_key_id}",
        json={"rpm_limit": 1},
    )
    assert edited.status_code == 200, edited.text
    taken = await _runtime(app).limiter.take(
        per_minute_bucket(1), key_bucket_key(workspace.gateway_key_id)
    )
    assert taken is None


def _key_prefix(workspace: GatewayWorkspace) -> str:
    parsed = parse_key(workspace.gateway_key, GATEWAY_KEY_PREFIX)
    assert parsed is not None
    return parsed.prefix


async def _all_spans(sessions: Sessions) -> list[dict[str, Any]]:
    await drain_recorder()
    async with sessions() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        rows = await db.execute(text("SELECT * FROM spans"))
        return [dict(row) for row in rows.mappings()]


async def test_key_rpm_refusal_stores_one_gateway_kind_span(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    await _limit_key(gateway_app, gateway_workspace)

    response = await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)

    assert response.status_code == 429, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    attributes: dict[str, Any] = span["attributes"]
    assert span["status"] == "error"
    assert attributes["spanlight.gateway.status"] == 429
    assert attributes["spanlight.gateway.error_code"] == "RATE_LIMITED"
    assert (span["error_class"], span["rate_limit_kind"]) == ("rate_limit", "gateway")
    assert span["source_key_id"] == gateway_workspace.gateway_key_id
    assert attributes["spanlight.gateway.request_id"] == response.headers["x-request-id"]
    assert attributes["spanlight.gateway.surface"] == "chat_completions"
    # The body was never read: the model, the input and what it would hash to are unknown.
    assert (span["model"], span["provider"], span["request_hash"]) == (None, None, None)
    assert span["name"] == "chat_completions"
    assert attributes["spanlight.gateway.stream"] is None
    # No provider answered: no provider snapshot. The `Retry-After` the client was told is kept.
    assert "provider.ratelimit" not in attributes
    retry_after_s = attributes["spanlight.gateway.retry_after_s"]
    assert 0 < retry_after_s <= int(response.headers["retry-after"])
    # Not served, so not counted in the key's minute.
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (0, 0)
    assert attributes["spanlight.gateway.refusals_suppressed"] == 0


async def test_refusals_ignore_a_pinned_trace_and_each_start_their_own(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    await _limit_key(gateway_app, gateway_workspace)
    pinned = {"x-spanlight-trace-id": "ab" * 16, "x-spanlight-parent-span-id": "cd" * 8}

    for _ in range(2):  # refusals may take BACKLOG // 2 places
        response = await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY, headers=pinned)
        assert response.status_code == 429, response.text

    spans = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    assert len(spans) == 2
    assert len({span["trace_id"] for span in spans}) == 2
    assert all(span["trace_id"] != pinned["x-spanlight-trace-id"] for span in spans)
    assert all(span["parent_span_id"] is None for span in spans)


async def test_refusals_within_a_second_leave_one_span(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(refusal_span, "_sampler", RefusalSampler(interval_s=3600.0))
    await _limit_key(gateway_app, gateway_workspace)

    responses = [await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY) for _ in range(5)]

    assert [r.status_code for r in responses] == [429] * 5  # every client still gets its 429
    [span] = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    assert span["attributes"]["spanlight.gateway.refusals_suppressed"] == 0
    assert refusal_span.refusal_sampler().pending(gateway_workspace.gateway_key_id) == 4


@pytest.mark.parametrize(
    ("path", "body", "envelope"),
    [
        (CALL_PATH, CHAT_BODY, "openai"),
        ("/gw/v1/messages", MESSAGES_BODY, "anthropic"),
    ],
)
async def test_refusal_response_bytes_unchanged(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
    path: str,
    body: dict[str, Any],
    envelope: str,
) -> None:
    await _limit_key(gateway_app, gateway_workspace)

    response = await gateway_workspace.gw.post(path, json=body)

    assert response.status_code == 429
    assert set(response.headers) == REFUSAL_HEADERS
    request_id = response.headers["x-request-id"]
    retry_after = int(response.headers["retry-after"])
    expected = render_error(
        rate_limited("rpm", 1, retry_after, key_prefix=_key_prefix(gateway_workspace)),
        "anthropic" if envelope == "anthropic" else "openai",
        request_id,
    )
    assert response.headers["x-spanlight-code"] == "RATE_LIMITED"
    assert response.headers["content-type"] == "application/json"
    assert response.content == json.dumps(
        expected.body, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")
    ).encode("utf-8")
    assert len(await read_gateway_spans(session_factory, gateway_workspace.project_id)) == 1


async def test_refusal_with_full_recorder_queue_counts_an_overflow_loss_and_still_answers_429(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    session_factory: Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _limit_key(gateway_app, gateway_workspace)
    release = asyncio.Event()
    real_ingest = pipeline.ingest_spans

    async def held_ingest(*args: Any, **kwargs: Any) -> Any:
        await release.wait()  # nothing is written until released: every span stays pending
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", held_ingest)
    overflow = metric_delta(GATEWAY_RECORD_FAILURES, reason="overflow")

    responses = [await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY) for _ in range(5)]
    # A refusal flood took its half of the backlog; an admitted call still finds room.
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    admitted = await execute(chat_request(stream=False), await gateway_context())

    assert [r.status_code for r in responses] == [429] * 5
    assert admitted.status == 200
    assert overflow.value == 3  # refusals past BACKLOG // 2; the admitted span was kept
    losses = span_losses.loss_counter().drain()
    by_reason = {(project, source, reason): n for (project, source, reason, _), n in losses.items()}
    assert by_reason == {(gateway_workspace.project_id, "gateway", "overflow"): 3}
    release.set()
    spans = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    assert sorted(str(span["rate_limit_kind"]) for span in spans) == ["None", "gateway", "gateway"]


async def test_unauthenticated_request_records_nothing(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    session_factory: Sessions,
) -> None:
    await _limit_key(gateway_app, gateway_workspace)
    wrong_secret = gateway_workspace.gateway_key[:-1] + (
        "A" if gateway_workspace.gateway_key[-1] != "A" else "B"
    )
    keys = [None, "spl_gw_not_a_key", wrong_secret]

    for key in keys:
        headers = {"Authorization": f"Bearer {key}"} if key is not None else {}
        response = await gateway_client.post(CALL_PATH, json=CHAT_BODY, headers=headers)
        assert response.status_code == 401, response.text

    assert await _all_spans(session_factory) == []
    assert span_losses.loss_counter().drain() == {}


async def test_models_refusal_records_nothing(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    # `/models` makes no model call and never records a span, refused or not.
    await _limit_key(gateway_app, gateway_workspace)

    response = await gateway_workspace.gw.get("/gw/v1/models")

    assert response.status_code == 429, response.text
    assert await _all_spans(session_factory) == []


async def test_refusal_span_is_bound_to_the_keys_project(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    async with session_factory() as db, db.begin():
        other_project = await create_project(db)
    await _limit_key(gateway_app, gateway_workspace)

    response = await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)
    assert response.status_code == 429
    await drain_recorder()

    async def visible(project_id: uuid.UUID) -> int:
        async with session_factory() as db, db.begin():
            await bind_project(db, project_id)
            return int(await db.scalar(text("SELECT count(*) FROM spans")) or 0)

    assert await visible(gateway_workspace.project_id) == 1
    assert await visible(other_project) == 0


async def test_org_ceiling_refusal_is_recorded_like_a_key_limit(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(gateway_app)
    settings = runtime.settings.model_copy(update={"gateway_org_rpm_ceiling": 1})
    monkeypatch.setattr(
        gateway_app.state, "gateway_runtime", dataclasses.replace(runtime, settings=settings)
    )
    async with session_factory() as db, db.begin():
        await bind_project(db, gateway_workspace.project_id)
        org_id = await db.scalar(
            select(Project.org_id).where(Project.id == gateway_workspace.project_id)
        )
    assert org_id is not None
    assert await runtime.limiter.take(per_minute_bucket(1), org_bucket_key(org_id)) is None

    response = await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)

    assert response.status_code == 429, response.text
    assert "organization" in response.json()["error"]["message"]
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["attributes"]["spanlight.gateway.error_code"] == "RATE_LIMITED"
    assert (span["error_class"], span["rate_limit_kind"]) == ("rate_limit", "gateway")


async def test_a_backlog_of_one_still_records_a_refusal(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
) -> None:
    # `GATEWAY_RECORD_BACKLOG=1` is legal; half of it must still leave a refusal one place.
    context = await gateway_context(keyed=True)
    settings = context.settings.model_copy(update={"gateway_record_backlog": 1})
    context = dataclasses.replace(context, settings=settings)
    refused = GatewayRequest(surface="chat_completions", body={}, stream=False)

    record_refusal(refused, context, rate_limited("rpm", 1, 30, key_prefix="abc"))

    spans = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    assert [span["rate_limit_kind"] for span in spans] == ["gateway"]
    assert span_losses.loss_counter().drain() == {}
