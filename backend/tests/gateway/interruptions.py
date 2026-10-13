"""The ways a streamed gateway call is interrupted, as steps the tests can repeat.

Each scenario scripts the fake provider, makes one streamed chat completion through `execute`
and interrupts it; it returns what was scripted and what the client read, and asserts nothing
about the span, which is the caller's concern. The scenario tests check one run in detail; the
leak sweep runs them many times over.
"""

from dataclasses import dataclass

from app.gateway.context import GatewayContext, GatewayResult
from app.gateway.execute import execute
from tests.gateway.support import chat_frames, chat_request, read_chunks, read_to_end
from tests.gateway_support import Fail, FakeProvider, Stall


@dataclass(frozen=True)
class Interrupted:
    """One interrupted call: its result, the bytes scripted upstream and those the client read."""

    result: GatewayResult
    scripted: list[bytes]
    received: list[bytes]


async def disconnect_mid_stream(provider: FakeProvider, context: GatewayContext) -> Interrupted:
    """The client reads two chunks, then closes its stream (the usage chunk is never read)."""
    frames = chat_frames()
    # The role chunk, "Hello", "!", the finish chunk, then the usage chunk.
    script = [frames[0], frames[1], frames[2], frames[10], frames[11]]
    provider.reply_stream(script)
    result = await execute(chat_request(), context)
    assert result.stream is not None
    received = await read_chunks(result.stream, 2)
    await result.stream.aclose()
    return Interrupted(result, script, received)


async def close_unread(provider: FakeProvider, context: GatewayContext) -> Interrupted:
    """The client closes its stream, twice, without reading a byte."""
    frames = chat_frames()
    script = [frames[0], frames[1], frames[11]]
    provider.reply_stream(script)
    result = await execute(chat_request(), context)
    assert result.stream is not None
    await result.stream.aclose()
    await result.stream.aclose()  # closing twice is harmless and records nothing more
    return Interrupted(result, script, [])


async def upstream_breaks(provider: FakeProvider, context: GatewayContext) -> Interrupted:
    """The provider's connection resets after two chunks; the client reads to the end."""
    frames = chat_frames()
    script = [frames[0], frames[1]]
    provider.reply_stream([*script, Fail()])
    result = await execute(chat_request(), context)
    assert result.stream is not None
    return Interrupted(result, script, await read_to_end(result.stream))


async def upstream_goes_quiet(provider: FakeProvider, context: GatewayContext) -> Interrupted:
    """The provider says nothing after its first chunk; needs the `short_idle_timeout` fixture."""
    frames = chat_frames()
    provider.reply_stream([frames[0], Stall(), *frames[1:]])
    result = await execute(chat_request(), context)
    assert result.stream is not None
    return Interrupted(result, frames, await read_to_end(result.stream))
