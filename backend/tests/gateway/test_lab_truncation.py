"""The Integration Lab's `truncated_stream` fault: a stream that ends early, cleanly, on purpose.

The client gets the first `after_chunks` frames byte for byte and then a clean end of stream,
without the final event (`[DONE]`), which is what SDKs detect. The span is a failure that names
the fault: the client got a success status and an answer it cannot trust.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.gateway.support import CHAT_BODY, chat_frames
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_span

AFTER_CHUNKS = 3


async def _truncate_every_stream(workspace: GatewayWorkspace) -> uuid.UUID:
    """A `truncated_stream` profile that always fires, attached to the workspace's key."""
    project = f"/api/v1/projects/{workspace.project_id}/gateway"
    created = await workspace.owner.post(
        f"{project}/fault-profiles",
        json={
            "name": "cut streams",
            "scenario": "truncated_stream",
            "params": {"after_chunks": AFTER_CHUNKS},
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    attached = await workspace.owner.patch(
        f"{project}/keys/{workspace.gateway_key_id}", json={"fault_profile_id": profile_id}
    )
    assert attached.status_code == 200, attached.text
    return uuid.UUID(profile_id)


async def _stream(workspace: GatewayWorkspace) -> tuple[int, bytes]:
    """A streamed chat completion through the key: its status and every byte the client got."""
    async with workspace.gw.stream(
        "POST", "/gw/v1/chat/completions", json={**CHAT_BODY, "stream": True}
    ) as response:
        return response.status_code, b"".join([chunk async for chunk in response.aiter_raw()])


async def test_truncated_stream_fault_ends_without_the_final_event(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    profile_id = await _truncate_every_stream(gateway_workspace)
    frames = chat_frames()
    fake_provider.reply_stream(frames)

    async with gateway_workspace.gw.stream(
        "POST", "/gw/v1/chat/completions", json={**CHAT_BODY, "stream": True}
    ) as response:
        received = b"".join([chunk async for chunk in response.aiter_raw()])

    assert response.status_code == 200
    assert response.headers["x-spanlight-fault"] == "truncated_stream"
    assert received == b"".join(frames[:AFTER_CHUNKS])  # a clean end, then nothing
    assert b"[DONE]" not in received
    assert fake_provider.open_streams == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert span["status_message"].startswith("200 FAULT_TRUNCATED_STREAM: ")
    assert span["attributes"]["spanlight.fault.scenario"] == "truncated_stream"
    assert span["attributes"]["spanlight.fault.profile_id"] == str(profile_id)
    assert span["attributes"]["spanlight.gateway.client_disconnected"] is False
    assert span["output"] == {"role": "assistant", "content": "Hello!"}
    # The usage chunk was past the cut: unknown usage, so no cost (never 0).
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (None, None, None)


async def test_a_stream_shorter_than_the_cut_passes_whole_and_is_not_a_failure(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _truncate_every_stream(gateway_workspace)
    frames = chat_frames()
    short = [frames[0], frames[12]]  # the role chunk, then `[DONE]`: two frames, under the cut
    fake_provider.reply_stream(short)

    status, received = await _stream(gateway_workspace)

    assert (status, received) == (200, b"".join(short))
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["status_message"]) == ("ok", None)
    assert span["attributes"]["spanlight.fault.scenario"] == "truncated_stream"


@pytest.mark.parametrize("line_end", ["\n", "\r\n"])
async def test_a_cut_inside_one_upstream_chunk_sends_exactly_the_first_frames(
    line_end: str,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _truncate_every_stream(gateway_workspace)
    frames = [frame.replace(b"\n", line_end.encode()) for frame in chat_frames()]
    fake_provider.reply_stream([b"".join(frames)])  # the whole stream in one chunk

    status, received = await _stream(gateway_workspace)

    assert status == 200
    # Cut mid-chunk, right after the third frame's blank line (a CRLF one whole).
    assert received == b"".join(frames[:AFTER_CHUNKS])
    assert fake_provider.open_streams == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert span["status_message"].startswith("200 FAULT_TRUNCATED_STREAM: ")
