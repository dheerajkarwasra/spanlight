"""HTTP stream transmission shares the provider lifetime and joins retained cleanup."""

import asyncio

import structlog
from starlette.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from app.gateway.context import GatewayResult

logger = structlog.get_logger(__name__)
_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class GatewayStreamingResponse(StreamingResponse):
    """A provider stream, always closed when the response ends, however it ends."""

    def __init__(self, result: GatewayResult) -> None:
        if result.stream is None:
            raise ValueError("A streaming response requires a stream")
        # No cache may keep a stream, and no proxy in front (nginx and the like) may buffer it:
        # tokens must reach the client as they arrive.
        super().__init__(
            result.stream, status_code=result.status, headers={**result.headers, **_STREAM_HEADERS}
        )
        self._stream = result.stream
        self._result = result

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        send_pending = False

        async def transmit(message: Message) -> None:
            nonlocal send_pending
            send_pending = True
            await send(message)
            send_pending = False

        try:
            async with asyncio.timeout_at(self._result.stream_deadline):
                await super().__call__(scope, receive, transmit)
        except TimeoutError:
            if send_pending:
                self._result.stream_error = "the response send exceeded the stream lifetime"
                logger.warning("gateway.stream_response_timeout")
            elif self._result.stream_error is None:
                self._result.stream_error = "the stream exceeded its lifetime"
                logger.warning("gateway.stream_total_timeout")
            raise
        finally:
            # Covers a client gone before the first chunk and a send that failed: closing writes
            # the span now instead of whenever the generator is collected. Idempotent.
            # Keep admission until the retained finalizer completes, even under repeated cancel.
            cancelled = False
            while True:
                try:
                    await self._stream.aclose()
                    break
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
