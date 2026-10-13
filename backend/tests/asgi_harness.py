"""Driving the app as an ASGI server would, one message at a time, with a client that can leave.

httpx's `ASGITransport` hands back a response once the app is done with it, which hides when
each message was delivered and cannot model a client that goes away mid-response. `call_asgi`
sends one HTTP request to the app and returns every message the app sent, in order.

`on_message` sees each message as it is sent. Raising `ClientGone` from it is the client
leaving: the `send` that delivered the message raises it, like a server's `send` once the
connection is closed (uvicorn raises an `OSError` subclass), every later `send` raises it too, and
`receive` answers `http.disconnect` from then on. Until then `receive` hands over the request body
once and then waits, as a client that stays until the response is over: answering at once would
make a streaming response's disconnect listener spin without yielding.

`spec_version` is the ASGI spec version the "server" announces in the scope (none by default,
which Starlette reads as 2.0); Starlette streams a response differently from 2.4 on (it lets
`send` raise instead of listening for a disconnect).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

Message = dict[str, Any]
OnMessage = Callable[[Message], Awaitable[None]]


class ClientGone(OSError):  # noqa: N818 - named for what happened, like `ClientDisconnect`
    """The client closed the connection: what `send` raises from then on."""


async def call_asgi(
    app: Any,
    path: str,
    *,
    method: str = "POST",
    headers: Sequence[tuple[str, str]] = (),
    body: bytes = b"",
    on_message: OnMessage | None = None,
    spec_version: str | None = None,
) -> list[Message]:
    """Send one request to `app` and return the messages it sent (see the module docstring)."""
    delivered: list[Message] = []
    gone = asyncio.Event()
    body_sent = False

    async def receive() -> Message:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if gone.is_set():
            raise ClientGone("the client has gone away")
        delivered.append(message)
        if on_message is None:
            return
        try:
            await on_message(message)
        except ClientGone:
            gone.set()
            raise

    await app(_scope(method, path, headers, spec_version), receive, send)
    return delivered


def is_last_body(message: Message) -> bool:
    """Whether `message` ends the response body."""
    return message["type"] == "http.response.body" and not message.get("more_body")


def _scope(
    method: str, path: str, headers: Sequence[tuple[str, str]], spec_version: str | None
) -> dict[str, Any]:
    asgi = {"version": "3.0"} | ({"spec_version": spec_version} if spec_version else {})
    return {
        "type": "http",
        "asgi": asgi,
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            *((name.lower().encode(), value.encode()) for name, value in headers),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
