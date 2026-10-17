"""Gateway-made errors get their class from the code table, read back from the stored span.

One test per row of the gateway's code table that a real call can produce: through `/gw/v1` with the
workspace's key, or `execute` on a context. Each asserts the span's `error_class`, its
`spanlight.gateway.error_code` and its `spanlight.gateway.status`. `RATE_LIMITED` is refused by
the router before `execute` (`key_limits.check_limits`), which records the refusal's span itself
(`app.gateway.refusal_span`); its test spends the key's one request a minute first.

A database too busy to answer inside `execute` (a pool timeout in the budget check, say) is a
`503 SERVICE_UNAVAILABLE` with `Retry-After`, class `gateway`, logged as a warning without a
traceback.
"""

import dataclasses
import logging
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import RETRY_AFTER_SECONDS
from app.gateway.budget import BudgetDecision
from app.gateway.execute import execute
from app.gateway.http import build_http_client
from app.gateway.limits import key_bucket_key, per_minute_bucket
from app.gateway.runtime import GatewayRuntime
from tests.gateway.support import CHAT_BODY, chat_request
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import Fail, FakeProvider, logged, read_gateway_span

Sessions = async_sessionmaker[AsyncSession]


def _assert_gateway_error(
    span: dict[str, Any], code: str, status: int, error_class: str, kind: str | None = None
) -> None:
    attributes: dict[str, Any] = span["attributes"]
    assert span["status"] == "error"
    assert attributes["spanlight.gateway.error_code"] == code
    assert attributes["spanlight.gateway.status"] == status
    assert span["error_class"] == error_class
    assert span["rate_limit_kind"] == kind


class _Guard:
    """A budget guard that answers `decision`, or raises `failure` when one is given."""

    def __init__(
        self, decision: BudgetDecision | None = None, failure: Exception | None = None
    ) -> None:
        self.decision, self.failure = decision, failure

    async def check(self, *_: Any, **__: Any) -> BudgetDecision:
        if self.failure is not None:
            raise self.failure
        assert self.decision is not None
        return self.decision


async def _private_resolver(host: str, port: int) -> Sequence[str]:
    """Every host resolves to a private address, which the egress policy refuses."""
    return ["10.0.0.5"]


async def test_model_not_allowed_is_client(
    gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    workspace = gateway_workspace
    edited = await workspace.owner.patch(
        f"/api/v1/projects/{workspace.project_id}/gateway/keys/{workspace.gateway_key_id}",
        json={"allowed_models": ["gpt-4o"]},
    )
    assert edited.status_code == 200, edited.text

    response = await workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)

    # The OpenAI envelope answers an unknown model with 404 `model_not_found`.
    assert response.status_code == 404, response.text
    span = await read_gateway_span(session_factory, workspace.project_id)
    _assert_gateway_error(span, "MODEL_NOT_ALLOWED", 404, "client")


async def test_no_compatible_target_is_client(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
) -> None:
    # A chat completion on a route whose only target is an Anthropic credential.
    context = await gateway_context(credential="anthropic")

    result = await execute(chat_request(stream=False), context)

    assert result.status == 400
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "NO_COMPATIBLE_TARGET", 400, "client")


async def test_nan_body_is_client_with_gateway_status_400(
    gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    # Pilot defect D11: refused before any attempt, so no upstream status exists; the span was
    # `unknown` until the gateway code decided the class.
    body = b'{"model": "gpt-4.1-mini", "messages": [], "temperature": NaN}'

    response = await gateway_workspace.gw.post(
        "/gw/v1/chat/completions", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400, response.text
    assert response.headers["X-Spanlight-Code"] == "INVALID_REQUEST"
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "INVALID_REQUEST", 400, "client")
    assert span["attributes"].get("spanlight.gateway.upstream_status") is None


async def test_budget_exceeded_is_gateway(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
) -> None:
    blocked = BudgetDecision(allowed=False, budget_id=uuid.uuid4(), reason="monthly cap reached")
    context = dataclasses.replace(await gateway_context(), budget_guard=_Guard(blocked))

    result = await execute(chat_request(stream=False), context)

    assert result.status == 402
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "BUDGET_EXCEEDED", 402, "gateway")


async def test_upstream_timeout_is_timeout(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Sessions,
) -> None:
    fake_provider.reply_stream([Fail(httpx.ReadTimeout("timed out"))])
    context = await gateway_context(credential="openai")  # one attempt: no retry to script

    result = await execute(chat_request(), context)

    assert result.status == 504
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "UPSTREAM_TIMEOUT", 504, "timeout")


async def test_upstream_unreachable_is_network(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Sessions,
) -> None:
    fake_provider.reply_stream([Fail(httpx.ConnectError("connection refused"))])
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(stream=False), context)

    assert result.status == 502
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "UPSTREAM_UNREACHABLE", 502, "network")


async def test_upstream_blocked_is_gateway(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Sessions,
) -> None:
    context = await gateway_context(credential="openai")
    http = build_http_client(context.settings, transport=fake_provider, resolver=_private_resolver)
    try:
        result = await execute(chat_request(stream=False), dataclasses.replace(context, http=http))
    finally:
        await http.aclose()

    assert result.status == 502
    assert fake_provider.requests == []  # refused before anything was sent
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "UPSTREAM_BLOCKED", 502, "gateway")


async def test_rate_limited_is_rate_limit_of_kind_gateway(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    workspace = gateway_workspace
    edited = await workspace.owner.patch(
        f"/api/v1/projects/{workspace.project_id}/gateway/keys/{workspace.gateway_key_id}",
        json={"rpm_limit": 1},
    )
    assert edited.status_code == 200, edited.text
    runtime = gateway_app.state.gateway_runtime
    assert isinstance(runtime, GatewayRuntime)
    bucket = key_bucket_key(workspace.gateway_key_id)
    assert await runtime.limiter.take(per_minute_bucket(1), bucket) is None  # the minute's one

    response = await workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 429, response.text
    span = await read_gateway_span(session_factory, workspace.project_id)
    _assert_gateway_error(span, "RATE_LIMITED", 429, "rate_limit", kind="gateway")


async def test_pool_timeout_in_budget_check_answers_503_with_retry_after_and_class_gateway(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
    caplog: pytest.LogCaptureFixture,
) -> None:
    busy = PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached")
    context = dataclasses.replace(await gateway_context(), budget_guard=_Guard(failure=busy))
    request = chat_request(stream=False)

    with caplog.at_level(logging.WARNING):
        result = await execute(request, context)

    assert result.status == 503
    assert result.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert result.headers["X-Spanlight-Code"] == "SERVICE_UNAVAILABLE"
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_gateway_error(span, "SERVICE_UNAVAILABLE", 503, "gateway")
    [event] = logged(caplog, "gateway.db_busy")
    assert (event["request_id"], event["error_type"]) == (request.request_id, "TimeoutError")
    [record] = [r for r in caplog.records if r.msg is event]
    assert (record.levelno, record.exc_info) == (logging.WARNING, None)
    assert logged(caplog, "gateway.execute_failed") == []
