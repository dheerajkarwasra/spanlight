"""The fake provider answers from the recorded bodies and nothing else."""

import json
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from fake_provider import RESPONSES_DIR, ServerHandle, serve

RECORD_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def provider() -> Iterator[ServerHandle]:
    handle = serve(RESPONSES_DIR)
    yield handle
    handle.close()


def _post(
    provider: ServerHandle, path: str, scenario: str | None, **body: object
) -> httpx.Response:
    headers = {"authorization": "Bearer sk-fixture"}
    if scenario is not None:
        headers["x-fixture-scenario"] = scenario
    return httpx.post(provider.url + path, headers=headers, json=body)


def test_chat_scenario_returns_the_recorded_body(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/chat/completions", None, model="gpt-4.1-mini", messages=[])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == json.loads((RESPONSES_DIR / "openai" / "chat.json").read_text())


def test_scenario_header_picks_the_body(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/chat/completions", "chat_tools", model="gpt-4.1-mini")

    assert response.json()["choices"][0]["finish_reason"] == "tool_calls"


def test_stream_scenario_is_sse(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/chat/completions", "chat_stream", stream=True)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert response.content == (RESPONSES_DIR / "openai" / "chat_stream.sse").read_bytes()
    assert response.text.rstrip().endswith("data: [DONE]")


def test_responses_route_defaults_to_the_responses_scenario(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/responses", None, model="gpt-4.1-mini", input="hi")

    assert response.json()["object"] == "response"


def test_error_scenario_is_429_with_retry_after(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/chat/completions", "error_429", stream=True)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


def test_anthropic_messages_cache_write_body(tmp_path: Path) -> None:
    body = {"id": "msg_test", "usage": {"input_tokens": 19, "cache_creation_input_tokens": 6404}}
    (tmp_path / "anthropic").mkdir()
    (tmp_path / "anthropic" / "messages_cache_write.json").write_text(json.dumps(body))
    handle = serve(tmp_path)
    try:
        response = _post(handle, "/v1/messages", "messages_cache_write", model="claude-haiku-4-5")
    finally:
        handle.close()

    assert response.status_code == 200
    assert response.json() == body


def test_missing_body_file_is_404_naming_the_file(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/messages", "messages_cache_write", model="claude-haiku-4-5")

    assert response.status_code == 404
    message = response.json()["error"]["message"]
    assert "responses/anthropic/messages_cache_write.json" in message
    assert "capture_bodies.py" in message


def test_unknown_scenario_is_404(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/chat/completions", "no_such_scenario")

    assert response.status_code == 404
    assert "no_such_scenario" in response.json()["error"]["message"]


def test_unknown_route_is_404(provider: ServerHandle) -> None:
    response = _post(provider, "/v1/models", None)

    assert response.status_code == 404


def test_main_prints_the_bound_port() -> None:
    with subprocess.Popen(
        [sys.executable, "fake_provider.py"],
        cwd=RECORD_DIR,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            port = int(process.stdout.readline())
            response = httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", json={})
            assert response.status_code == 200
        finally:
            process.kill()


def raw_status(port: int, path: str, content_length: str) -> int:
    """POST with a hand-written Content-Length and no body; return the status code."""
    request = (
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {content_length}\r\n"
        "Connection: close\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request.encode())
        status_line = sock.makefile("rb").readline()
    return int(status_line.split()[1])


def test_oversized_body_is_413(provider: ServerHandle) -> None:
    assert raw_status(provider.port, "/v1/chat/completions", str(6 * 1024 * 1024)) == 413


def test_bad_content_length_is_400(provider: ServerHandle) -> None:
    assert raw_status(provider.port, "/v1/chat/completions", "many") == 400
