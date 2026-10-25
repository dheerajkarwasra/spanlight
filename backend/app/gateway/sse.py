"""Incremental server-sent-events frame parser and the shared stream-summary types.

Pure module: bytes in, frames out. The parser only reads copies of what the gateway
forwards, so a frame it cannot understand never affects the client's stream.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_FRAME_BYTES = 1_000_000
MAX_OUTPUT_BYTES = 1_000_000

# A frame ends at an empty line; each line ends with CRLF, LF or CR. The atomic groups stop
# the regex from backtracking so that a CRLF is never read as a CR followed by an LF.
FRAME_END = re.compile(rb"(?>\r\n|\r|\n)(?>\r\n|\r|\n)")


class FrameTooLarge(Exception):  # noqa: N818 - name is part of the module contract
    """A single SSE frame grew past ``MAX_FRAME_BYTES``."""


@dataclass(frozen=True, slots=True)
class SseFrame:
    event: str | None
    data: str


@dataclass(slots=True)
class SseState:
    """Parser state carried between chunks: the bytes of the unfinished frame."""

    buffer: bytearray = field(default_factory=bytearray)
    scanned: int = 0


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage in the native convention: ``input_tokens`` is the whole prompt.

    Cache reads (``cached_tokens``) and cache writes (``cache_write_tokens``, of which
    ``cache_write_1h_tokens`` were written with the 1-hour TTL) are parts of ``input_tokens``.
    A part is ``None`` when the provider did not report it, never 0. ``output_tokens`` is
    ``None`` when the provider never sent its final count (a stream cut before its last usage
    event): the call's cost is then unknown, and Anthropic's ``output_tokens: 1`` placeholder
    from ``message_start`` is never taken for it.

    ``service_tier`` is one of the four stored values (``normalize_tier``) or ``None``; a
    spelling that did not normalize is kept in ``service_tier_raw`` for the span's attributes.
    ``web_search_requests`` is the provider's count of billed server-side web searches.
    """

    input_tokens: int
    output_tokens: int | None
    cached_tokens: int | None  # None when the provider sent no cache detail
    cache_write_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    service_tier: str | None = None
    web_search_requests: int | None = None
    service_tier_raw: str | None = None


@dataclass(frozen=True, slots=True)
class StreamSummary:
    """What an observer learned from a stream; ``None`` means the stream did not say.

    ``usage_complete`` is True only when the provider's final usage was seen, so a stream cut
    short is told from one that never reports usage. ``saw_output`` says whether any part of
    an answer arrived, which is what makes a missing final usage a cut rather than an empty call.
    ``error`` is set when the provider sent an error event inside the stream: ``<type>: <message>``.
    """

    usage: Usage | None = None
    model: str | None = None
    finish_reason: str | None = None
    output: dict[str, Any] | None = None
    response_id: str | None = None
    output_truncated: bool = False  # output stopped growing at the observer's memory cap
    usage_complete: bool = False
    saw_output: bool = False
    error: str | None = None


class StreamObserver(Protocol):
    def on_frame(self, frame: SseFrame) -> None: ...

    def summary(self) -> StreamSummary: ...


def parse_frames(chunk: bytes, state: SseState) -> list[SseFrame]:
    """Feed one chunk and return the frames it completed (frames may span chunks)."""
    state.buffer += chunk
    frames: list[SseFrame] = []
    while True:
        match = FRAME_END.search(state.buffer, state.scanned)
        if match is None:
            # Keep the last 3 bytes in play: a CRLF CRLF terminator may be cut anywhere.
            state.scanned = max(0, len(state.buffer) - 3)
            break
        if match.end() > MAX_FRAME_BYTES:
            raise FrameTooLarge(f"SSE frame larger than {MAX_FRAME_BYTES} bytes")
        block = bytes(state.buffer[: match.start()])
        del state.buffer[: match.end()]
        state.scanned = 0
        frame = _parse_block(block)
        if frame is not None:
            frames.append(frame)
    if len(state.buffer) > MAX_FRAME_BYTES:
        raise FrameTooLarge(f"SSE frame larger than {MAX_FRAME_BYTES} bytes")
    return frames


def _parse_block(block: bytes) -> SseFrame | None:
    event: str | None = None
    data_lines: list[str] = []
    for raw in re.split(rb"\r\n|\r|\n", block):
        if not raw or raw.startswith(b":"):
            continue
        name, _, value = raw.decode("utf-8", errors="replace").partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            event = value
        elif name == "data":
            data_lines.append(value)
    if event is None and not data_lines:
        return None
    return SseFrame(event=event, data="\n".join(data_lines))


class OutputBudget:
    """Caps what an observer accumulates; ``take`` is False once the cap is reached."""

    def __init__(self) -> None:
        self._used = 0
        self.truncated = False

    def take(self, text: str) -> bool:
        return self.spend(len(text) + 1)

    def spend(self, size: int) -> bool:
        if self.truncated:
            return False
        self._used += size
        if self._used > MAX_OUTPUT_BYTES:
            self.truncated = True
            return False
        return True


def json_object(data: str) -> dict[str, Any] | None:
    """Parse frame data as a JSON object; anything else (``[DONE]``, junk) is ``None``."""
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def error_text(kind: object, message: object) -> str:
    """``<type or code>: <message>`` of a provider's in-stream error, with the parts it sent."""
    code = as_int(kind)
    parts = (as_str(kind) or (str(code) if code is not None else None), as_str(message))
    return ": ".join(part for part in parts if part) or "error"


def dict_at(obj: object, *path: str) -> dict[str, Any]:
    """Walk nested dicts; a missing or non-dict step yields an empty dict."""
    current: object = obj
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return current if isinstance(current, dict) else {}
