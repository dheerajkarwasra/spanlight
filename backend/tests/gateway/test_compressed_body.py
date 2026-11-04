"""A gzip or deflate gateway body may inflate at most `MAX_COMPRESSION_RATIO`-fold, as on ingest.

A few kilobytes of gzip that inflate to megabytes of JSON are cheap to send and costly to parse,
serialise and record, so such a body is answered `413 PAYLOAD_TOO_LARGE` before it is parsed,
and no provider is called. A compressed body at an ordinary ratio is served as before.
"""

import base64
import gzip
import json
import os
import zlib
from collections.abc import Callable

import pytest

from app.ingest.schemas import MAX_COMPRESSION_RATIO
from tests.gateway.support import CHAT_BODY
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, fixture_text

CHAT_PATH = "/gw/v1/chat/completions"
MESSAGES_PATH = "/gw/v1/messages"


def _chat(content: str) -> bytes:
    return json.dumps({**CHAT_BODY, "messages": [{"role": "user", "content": content}]}).encode()


@pytest.mark.parametrize(
    ("path", "encoding", "compress"),
    [
        (CHAT_PATH, "gzip", gzip.compress),
        (MESSAGES_PATH, "deflate", zlib.compress),
    ],
)
async def test_a_body_that_inflates_past_the_ratio_is_refused_before_any_provider_call(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    path: str,
    encoding: str,
    compress: Callable[[bytes], bytes],
) -> None:
    inflated = _chat("a" * (4 * 1024 * 1024))  # 4 MiB of JSON from a few kilobytes
    sent = compress(inflated)
    assert len(inflated) > MAX_COMPRESSION_RATIO * len(sent)

    response = await gateway_workspace.gw.post(
        path,
        content=sent,
        headers={"content-type": "application/json", "content-encoding": encoding},
    )

    assert response.status_code == 413, response.text
    error = response.json()["error"]
    assert error["spanlight_code"] == "PAYLOAD_TOO_LARGE"
    assert f"{MAX_COMPRESSION_RATIO}-fold" in error["message"]
    assert fake_provider.requests == []


async def test_a_compressed_body_at_an_ordinary_ratio_is_served(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    # Over the 1 MiB floor where the ratio is checked, and compressing only about 1.3:1.
    inflated = _chat(base64.b64encode(os.urandom(1024 * 1024)).decode())
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())

    response = await gateway_workspace.gw.post(
        CHAT_PATH,
        content=gzip.compress(inflated),
        headers={"content-type": "application/json", "content-encoding": "gzip"},
    )

    assert response.status_code == 200, response.text
    assert len(fake_provider.requests) == 1
