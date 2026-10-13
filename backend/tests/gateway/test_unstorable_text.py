"""A gateway call whose body Postgres could not store as sent still leaves its one span.

The gateway parses the client's body leniently and forwards it as sent, so a prompt may hold a
NUL or a lone surrogate (`\\u0000`, `\\ud800`) and a number may be NaN or Infinity. None of these
fits in `jsonb`. The span stores the prompt with each NUL removed, each lone surrogate replaced
by `?`, and each non-finite number as its name, and no span is counted lost. A body with NaN
or Infinity cannot be forwarded as strict JSON, so it is answered `INVALID_REQUEST`, and that
answer's span is stored too. A model name with a NUL or a lone surrogate is refused before the
call starts, like any body without a usable `model`: no provider serves it, and the budget and
route checks could not look it up.
"""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.core.redact import DEPTH_MARKER
from tests.gateway.support import CHAT_BODY, MESSAGES_BODY
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import (
    FakeProvider,
    fixture_text,
    metric_delta,
    read_gateway_span,
    read_gateway_spans,
)

CHAT_PATH = "/gw/v1/chat/completions"
MESSAGES_PATH = "/gw/v1/messages"
# Deeper than Python's recursion limit allows a recursive walk, well within what `json` reads.
NESTING = 1000


async def _post_raw(workspace: GatewayWorkspace, raw: str, path: str = CHAT_PATH) -> httpx.Response:
    """Send `raw` as the body: httpx's own JSON encoder refuses NaN and lone surrogates."""
    return await workspace.gw.post(
        path,
        content=raw.encode("utf-8", "surrogatepass"),
        headers={"content-type": "application/json"},
    )


def _with_prompt(content: str) -> str:
    """The chat body with `content` as its prompt, as ASCII JSON (`\\u` escapes)."""
    return json.dumps({**CHAT_BODY, "messages": [{"role": "user", "content": content}]})


async def _one_span_after(
    workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    sessions: async_sessionmaker[AsyncSession],
    raw: str,
    *,
    status: int = 200,
) -> dict[str, Any]:
    """The span of one call with body `raw`; fails unless it is stored and nothing was lost."""
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    rejected = metric_delta(GATEWAY_RECORD_FAILURES, reason="rejected")

    response = await _post_raw(workspace, raw)

    assert response.status_code == status, response.text
    span = await read_gateway_span(sessions, workspace.project_id)
    assert (errors.value, rejected.value) == (0, 0)
    return span


async def test_a_prompt_with_a_nul_leaves_one_span_without_it(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raw = _with_prompt("Hel\x00lo")
    assert "\\u0000" in raw

    span = await _one_span_after(gateway_workspace, fake_provider, session_factory, raw)

    assert span["input"]["messages"][0]["content"] == "Hello"


async def test_a_prompt_with_a_lone_surrogate_leaves_one_span_with_it_replaced(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raw = _with_prompt("Hel\ud800lo")
    assert "\\ud800" in raw

    span = await _one_span_after(gateway_workspace, fake_provider, session_factory, raw)

    assert span["input"]["messages"][0]["content"] == "Hel?lo"


async def test_nan_and_infinity_in_the_body_leave_one_span_with_their_names(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = {**CHAT_BODY, "temperature": float("nan"), "top_p": float("inf"), "seed": -1e999}
    raw = json.dumps(body)  # `NaN`, `Infinity`, `-Infinity`: not JSON, but Python reads them
    assert "NaN" in raw

    span = await _one_span_after(gateway_workspace, fake_provider, session_factory, raw, status=400)

    assert span["status_message"].startswith("400 INVALID_REQUEST")
    stored = span["input"]
    assert (stored["temperature"], stored["top_p"], stored["seed"]) == (
        "NaN",
        "Infinity",
        "-Infinity",
    )


async def test_a_body_nested_past_the_depth_cap_leaves_one_span_with_the_marker(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    nested = "[" * NESTING + "]" * NESTING
    raw = json.dumps(CHAT_BODY)[:-1] + f', "metadata": {nested}}}'
    assert json.loads(raw)["metadata"]  # Python reads it, so the gateway does too

    span = await _one_span_after(gateway_workspace, fake_provider, session_factory, raw)

    value = span["input"]["metadata"]
    depth = 0
    while isinstance(value, list):
        [value] = value
        depth += 1
    assert value == DEPTH_MARKER
    assert depth < NESTING


@pytest.mark.parametrize("odd", ["\x00", "\ud800"], ids=["nul", "lone_surrogate"])
@pytest.mark.parametrize(
    ("path", "body"),
    [(CHAT_PATH, CHAT_BODY), (MESSAGES_PATH, MESSAGES_BODY)],
    ids=["chat", "messages"],
)
async def test_a_model_name_postgres_cannot_take_is_refused_before_the_call(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    odd: str,
    path: str,
    body: dict[str, Any],
) -> None:
    raw = json.dumps({**body, "model": f"{body['model']}{odd}"})

    response = await _post_raw(gateway_workspace, raw, path)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["type"] == "invalid_request_error"  # in either envelope
    assert fake_provider.requests == []
    assert await read_gateway_spans(session_factory, gateway_workspace.project_id) == []
