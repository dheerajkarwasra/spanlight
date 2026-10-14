"""The mock provider must satisfy the real OpenAI and Anthropic clients, streaming or not."""

# ruff: noqa: S101 (asserts are what a test is made of)

import socket
import threading
from collections.abc import Iterator

import anthropic
import httpx
import mock_provider
import openai
import pytest
from mock_provider import MOCK_KEY


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = mock_provider.make_server(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_health(base_url: str) -> None:
    assert httpx.get(f"{base_url}/health").status_code == 200


def test_openai_client_gets_a_completion(base_url: str) -> None:
    client = openai.OpenAI(base_url=f"{base_url}/v1", api_key=MOCK_KEY)
    result = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hello there"}]
    )
    assert result.choices[0].message.content == "mock answer to: hello there"
    assert result.choices[0].finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens > 0
    assert result.model == "gpt-4o-mini"


def test_openai_stream_ends_with_usage(base_url: str) -> None:
    client = openai.OpenAI(base_url=f"{base_url}/v1", api_key=MOCK_KEY)
    stream = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hello there"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    chunks = list(stream)
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == "mock answer to: hello there"
    assert any(c.choices and c.choices[0].finish_reason == "stop" for c in chunks)
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens > 0
    assert chunks[-1].usage.completion_tokens > 0
    assert chunks[-1].model == "gpt-4o-mini"


def test_openai_paths_without_the_v1_prefix(base_url: str) -> None:
    # A credential whose base URL has no `/v1` makes the gateway call `/chat/completions`.
    client = openai.OpenAI(base_url=base_url, api_key=MOCK_KEY)
    result = client.chat.completions.create(
        model="m", messages=[{"role": "user", "content": "hello there"}]
    )
    assert result.choices[0].message.content == "mock answer to: hello there"
    assert [m.id for m in client.models.list()]


def test_anthropic_client_gets_a_message(base_url: str) -> None:
    client = anthropic.Anthropic(base_url=base_url, api_key=MOCK_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[{"role": "user", "content": "hello there"}],
    )
    assert message.content[0].text == "mock answer to: hello there"
    assert message.usage.input_tokens > 0
    assert message.usage.output_tokens > 0
    assert message.stop_reason == "end_turn"
    assert message.model == "claude-sonnet-4-5"


def test_anthropic_stream(base_url: str) -> None:
    client = anthropic.Anthropic(base_url=base_url, api_key=MOCK_KEY)
    with client.messages.stream(
        model="claude-sonnet-4-5",
        max_tokens=64,
        messages=[{"role": "user", "content": "hello there"}],
    ) as stream:
        final = stream.get_final_message()
    assert final.content[0].text == "mock answer to: hello there"
    assert final.usage.input_tokens > 0
    assert final.usage.output_tokens > 0
    assert final.stop_reason == "end_turn"


def test_last_user_message_wins_and_parts_are_joined(base_url: str) -> None:
    client = openai.OpenAI(base_url=f"{base_url}/v1", api_key=MOCK_KEY)
    result = client.chat.completions.create(
        model="m",
        messages=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ignored"},
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
        ],
    )
    assert result.choices[0].message.content == "mock answer to: second"
