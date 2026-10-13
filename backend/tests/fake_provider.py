"""The fake upstream LLM provider the gateway tests script (`FakeProvider`) and its stream steps.

Import these through `tests.gateway_support`, whose docstring describes them.
"""

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True)
class Stall:
    """Wait until `until` is set; `None` waits forever (until cancelled)."""

    until: asyncio.Event | None = None

    async def wait(self) -> None:
        await (self.until or asyncio.Event()).wait()


@dataclass(frozen=True)
class Fail:
    """Raise `error` from the stream; by default the connection breaks mid-body."""

    error: BaseException = field(default_factory=lambda: httpx.ReadError("connection reset"))


StreamStep = bytes | Stall | Fail


class ScriptedStream(httpx.AsyncByteStream):
    """A response body that plays its steps in order and reports opens and closes to its owner."""

    def __init__(self, steps: Sequence[StreamStep], owner: "FakeProvider") -> None:
        self._steps = list(steps)
        self._owner = owner
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for step in self._steps:
            if isinstance(step, Stall):
                await step.wait()
            elif isinstance(step, Fail):
                raise step.error
            else:
                yield step

    async def aclose(self) -> None:
        self._owner.aclose_calls += 1
        if not self.closed:
            self.closed = True
            self._owner.streams_closed += 1


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    json: Any  # the parsed body; None when there was no body or it was not JSON


@dataclass(frozen=True)
class _Scripted:
    status: int
    headers: dict[str, str]
    body: bytes | None
    steps: Sequence[StreamStep] | None
    before_headers: Stall | None


class FakeProvider(httpx.MockTransport):
    """The upstream LLM provider: answers each request with the next scripted response."""

    def __init__(self) -> None:
        super().__init__(self._handle)
        self.requests: list[RecordedRequest] = []
        self.unscripted: list[RecordedRequest] = []  # requests that arrived with no script left
        self.streams_opened = 0
        self.streams_closed = 0
        self.aclose_calls = 0
        self._script: deque[_Scripted] = deque()

    @property
    def open_streams(self) -> int:
        """Streams handed out whose `aclose()` has not run."""
        return self.streams_opened - self.streams_closed

    @property
    def pending(self) -> int:
        """Scripted responses not yet used."""
        return len(self._script)

    def reply_json(
        self,
        body: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        before_headers: Stall | None = None,
    ) -> None:
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self._script.append(
            _Scripted(
                status,
                {"content-type": "application/json", **(headers or {})},
                content,
                None,
                before_headers,
            )
        )

    def reply_stream(
        self,
        steps: Sequence[StreamStep],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        before_headers: Stall | None = None,
    ) -> None:
        self._script.append(
            _Scripted(
                status,
                {"content-type": "text/event-stream", **(headers or {})},
                None,
                list(steps),
                before_headers,
            )
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        recorded = _recorded(request)
        self.requests.append(recorded)
        if not self._script:
            self.unscripted.append(recorded)
            raise AssertionError(f"unscripted upstream request: {request.method} {request.url}")
        scripted = self._script.popleft()
        if scripted.before_headers is not None:
            await scripted.before_headers.wait()
        steps = scripted.steps if scripted.steps is not None else [scripted.body or b""]
        self.streams_opened += 1
        return httpx.Response(
            scripted.status, headers=scripted.headers, stream=ScriptedStream(steps, self)
        )


def _recorded(request: httpx.Request) -> RecordedRequest:
    try:
        body = json.loads(request.content) if request.content else None
    except ValueError:
        body = None
    return RecordedRequest(request.method, str(request.url), dict(request.headers), body)
