"""The usage chunk the gateway asks OpenAI for on a streaming chat completion, and hiding it.

Without `stream_options.include_usage` OpenAI sends no usage on a stream, and the call's span
would have no tokens or cost. So when the client set no `stream_options`, the gateway asks for
usage itself (`injects_usage`). OpenAI then ends the stream with one extra chunk whose `choices`
is empty and which carries `usage`. A client that never asked for it may not expect it (code
written as `chunk.choices[0].delta` raises on it), so `UsageChunkFilter` drops that chunk from
the client's stream while the observer still reads it. A client that asked for usage itself
gets the stream unchanged.

Pure: bytes in, bytes out.
"""

import re
from collections.abc import Mapping
from typing import Any

from app.gateway.sse import FRAME_END, MAX_FRAME_BYTES, json_object

_LINE_END = re.compile(rb"\r\n|\r|\n")


def injects_usage(surface: str, stream: bool, body: Mapping[str, Any]) -> bool:
    """Whether the gateway adds `stream_options.include_usage` to this call's upstream body."""
    return surface == "chat_completions" and stream and "stream_options" not in body


class UsageChunkFilter:
    """Forwards whole SSE frames, minus the usage-only chunk the gateway asked for.

    Bytes are held only until the frame they belong to is complete. A frame that grows past
    `MAX_FRAME_BYTES` without ending turns the filter off: from then on bytes pass unchanged.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._passthrough = False

    def feed(self, chunk: bytes) -> bytes:
        """The bytes of `chunk` that can go to the client now."""
        if self._passthrough:
            return chunk
        self._buffer += chunk
        held = bytes(self._buffer)
        out = bytearray()
        start = 0
        for match in FRAME_END.finditer(held):
            # A CR as the very last byte may be the first half of a CRLF still to come.
            if match.end() == len(held) and held.endswith(b"\r"):
                break
            frame = held[start : match.end()]
            if not _is_usage_only(frame):
                out += frame
            start = match.end()
        del self._buffer[:start]
        if len(self._buffer) > MAX_FRAME_BYTES:
            self._passthrough = True
            out += self._buffer
            self._buffer.clear()
        return bytes(out)

    def flush(self) -> bytes:
        """What is left when the stream ends: an unfinished frame, sent as it is."""
        rest = bytes(self._buffer)
        self._buffer.clear()
        return rest


def _is_usage_only(frame: bytes) -> bool:
    """A chunk with an empty `choices` list and a `usage` object."""
    if b'"usage"' not in frame:
        return False
    data = b"\n".join(
        line[5:].removeprefix(b" ") for line in _LINE_END.split(frame) if line.startswith(b"data:")
    )
    chunk = json_object(data.decode("utf-8", errors="replace"))
    if chunk is None:
        return False
    return chunk.get("choices") == [] and isinstance(chunk.get("usage"), dict)
