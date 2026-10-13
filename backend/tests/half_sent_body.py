"""Sending part of a request body and then leaving, as a server delivers a dropped upload.

httpx's ASGI transport always sends the whole body, so the request is driven by hand: one chunk
with `more_body`, then `http.disconnect`. An exception the app lets out (Starlette's error
middleware re-raises after answering 500) propagates and fails the calling test.
"""

from collections.abc import Sequence
from typing import Any

Message = dict[str, Any]


async def send_half_and_leave(
    app: Any, path: str, headers: Sequence[tuple[str, str]], first_chunk: bytes
) -> list[Message]:
    """POST `first_chunk` to `path` with more to come, then disconnect; the messages sent back."""
    pending: list[Message] = [
        {"type": "http.request", "body": first_chunk, "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "headers": [
            (b"host", b"testserver"),
            *((name.lower().encode(), value.encode()) for name, value in headers),
        ],
    }
    await app(scope, receive, send)
    return sent


def response_status(sent: list[Message]) -> int:
    [start] = [message for message in sent if message["type"] == "http.response.start"]
    status: int = start["status"]
    return status


def response_body(sent: list[Message]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
