"""Upstream bodies are refused before content decoding can bypass the response cap."""

import gzip

import httpx
import pytest

from app.gateway.adapters.base import UnsupportedEncoding, UpstreamRequest, send_upstream
from app.gateway.upstream_io import classify_failure, read_all
from tests.gateway_support import Fail, FakeProvider


async def test_compressed_upstream_is_closed_unread_and_identity_still_passes(
    fake_provider: FakeProvider,
) -> None:
    body = b'{"data":[]}'
    fake_provider.reply_stream(
        [Fail(AssertionError("compressed body must not be read")), gzip.compress(body)],
        headers={"content-encoding": "gzip"},
    )
    fake_provider.reply_json(body, headers={"content-encoding": "identity"})
    request = UpstreamRequest("GET", "https://provider.example/v1/models", {})

    async with httpx.AsyncClient(transport=fake_provider) as http:
        with pytest.raises(UnsupportedEncoding) as caught:
            await send_upstream(http, request, 1.0)

        assert fake_provider.streams_closed == 1
        assert fake_provider.open_streams == 0
        failure = classify_failure(caught.value, 1000)
        assert failure is not None
        assert failure.kind == "blocked"  # never retried, no fallback
        assert failure.error is not None
        assert failure.error.status == 502
        assert "content-encoding" in failure.error.message

        response = await send_upstream(http, request, 1.0)
        assert response.status == 200
        assert await read_all(response) == body

    assert fake_provider.streams_closed == 2
    assert fake_provider.open_streams == 0
