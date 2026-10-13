"""`read_bounded_body` on bodies that do not arrive whole: a client that leaves, a cut stream."""

import gzip
import zlib
from typing import Any

import pytest
from starlette.requests import Request

from app.core.body import ClientDisconnected, UndecodableBody, read_bounded_body

LIMIT = 1024 * 1024
DOCUMENT = b'{"spans":[]}'

Message = dict[str, Any]


def _request(messages: list[Message], *, encoding: str | None = None) -> Request:
    """A request whose ASGI `receive` hands over `messages` in order."""
    pending = list(messages)

    async def receive() -> Message:
        return pending.pop(0)

    headers = [(b"content-encoding", encoding.encode())] if encoding else []
    scope = {"type": "http", "method": "POST", "path": "/v1/traces", "headers": headers}
    return Request(scope, receive)


def _whole(body: bytes, *, encoding: str | None = None) -> Request:
    return _request([{"type": "http.request", "body": body, "more_body": False}], encoding=encoding)


async def test_a_client_disconnect_while_reading_is_reported_as_its_own_failure() -> None:
    request = _request(
        [
            {"type": "http.request", "body": b'{"spans":', "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    with pytest.raises(ClientDisconnected):
        await read_bounded_body(request, LIMIT)


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [("deflate", zlib.compress), ("gzip", gzip.compress)],
)
async def test_a_truncated_compressed_body_is_undecodable(encoding: str, compress: Any) -> None:
    cut = compress(DOCUMENT)[:-3]

    with pytest.raises(UndecodableBody):
        await read_bounded_body(_whole(cut, encoding=encoding), LIMIT)


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [("deflate", zlib.compress), ("gzip", gzip.compress)],
)
async def test_a_complete_compressed_body_decodes(encoding: str, compress: Any) -> None:
    body = await read_bounded_body(_whole(compress(DOCUMENT), encoding=encoding), LIMIT)

    assert body == DOCUMENT


async def test_bytes_after_a_complete_compressed_stream_are_ignored_as_before() -> None:
    body = await read_bounded_body(
        _whole(zlib.compress(DOCUMENT) + b"junk", encoding="deflate"), LIMIT
    )

    assert body == DOCUMENT
