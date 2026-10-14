#!/usr/bin/env python3
"""A deterministic stand-in for the OpenAI and Anthropic APIs.

It lets documentation samples run end to end without provider keys or network access:

    python scripts/docs-verify/mock_provider.py --port 9900

Endpoints (the OpenAI ones answer with and without a ``/v1`` prefix, because an
OpenAI-compatible base URL may or may not include it):

* ``POST /v1/chat/completions``  OpenAI Chat Completions, streaming or not
* ``POST /v1/messages``          Anthropic Messages, streaming or not
* ``GET  /v1/models``            a short model list
* ``GET  /health``               liveness, no key needed

The answer is always ``mock answer to: <last user message>``, so two prompts give two
distinguishable answers. The response echoes the requested model. Usage is the number of
whitespace-separated words, with a floor of one, so no token count is ever zero.
Calls must carry ``MOCK_KEY`` (as a bearer token or ``x-api-key``); anything else is a 401 in
the provider's own error shape, which proves that the caller sent the credential it was given.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MOCK_PORT = 9900
MOCK_KEY = "docs-verify-key"

MODELS = ("gpt-4o-mini", "claude-sonnet-4-5")


def count_tokens(text: str) -> int:
    """Words in ``text``, at least one."""
    return max(1, len(text.split()))


def _content_text(content: Any) -> str:
    """The text of a message body: a string, or a list of text parts (``{"type": "text", ...}``)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return " ".join(parts)
    return ""


def last_user_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _content_text(message.get("content"))
    return ""


def prompt_text(body: dict[str, Any]) -> str:
    """Everything the model was shown, for counting input tokens."""
    pieces: list[str] = []
    system = body.get("system")
    if system is not None:
        pieces.append(_content_text(system))
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            pieces.append(_content_text(message.get("content")))
    return " ".join(pieces)


def answer_for(body: dict[str, Any]) -> str:
    return f"mock answer to: {last_user_message(body.get('messages'))}"


def _words(text: str) -> list[str]:
    """Split into pieces that join back to ``text`` exactly (each piece keeps its leading space)."""
    pieces = text.split(" ")
    return [pieces[0]] + [" " + piece for piece in pieces[1:]]


def openai_completion(body: dict[str, Any]) -> dict[str, Any]:
    answer = answer_for(body)
    prompt_tokens = count_tokens(prompt_text(body))
    completion_tokens = count_tokens(answer)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", MODELS[0]),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def openai_stream_events(body: dict[str, Any]) -> list[dict[str, Any] | str]:
    """Chunks in the order OpenAI sends them, ending with the usage chunk and ``[DONE]``."""
    answer = answer_for(body)
    prompt_tokens = count_tokens(prompt_text(body))
    completion_tokens = count_tokens(answer)
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": body.get("model", MODELS[0]),
    }

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    events: list[dict[str, Any] | str] = [chunk({"role": "assistant", "content": ""})]
    events += [chunk({"content": piece}) for piece in _words(answer)]
    events.append(chunk({}, "stop"))
    options = body.get("stream_options")
    if isinstance(options, dict) and options.get("include_usage"):
        events.append(
            {
                **base,
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )
    events.append("[DONE]")
    return events


def anthropic_message(body: dict[str, Any]) -> dict[str, Any]:
    answer = answer_for(body)
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", MODELS[1]),
        "content": [{"type": "text", "text": answer}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": count_tokens(prompt_text(body)),
            "output_tokens": count_tokens(answer),
        },
    }


def anthropic_stream_events(body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Events in Anthropic's documented order: start, one text block, final delta, stop."""
    message = anthropic_message(body)
    input_tokens = message["usage"]["input_tokens"]
    output_tokens = message["usage"]["output_tokens"]
    started = {**message, "content": [], "stop_reason": None}
    started["usage"] = {"input_tokens": input_tokens, "output_tokens": 1}
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": started}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        ("ping", {"type": "ping"}),
    ]
    for piece in _words(message["content"][0]["text"]):
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )
        )
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return events


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "spanlight-docs-verify-mock"

    def log_message(self, format: str, *args: Any) -> None:
        return  # keep the test output quiet

    # -- plumbing ---------------------------------------------------------------------------

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _start_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _write(self, text: str) -> None:
        self.wfile.write(text.encode())
        self.wfile.flush()

    def _authorized(self) -> bool:
        bearer = self.headers.get("Authorization", "")
        return bearer == f"Bearer {MOCK_KEY}" or self.headers.get("x-api-key") == MOCK_KEY

    def _reject_key(self, anthropic_shape: bool) -> None:
        if anthropic_shape:
            self._send_json(
                401,
                {
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid x-api-key"},
                },
            )
        else:
            self._send_json(
                401,
                {
                    "error": {
                        "message": "Incorrect API key provided.",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
            )

    def _read_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None
        return body if isinstance(body, dict) else None

    # -- routes -----------------------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(200, {"status": "ok"})
        elif path in ("/v1/models", "/models"):
            if not self._authorized():
                self._reject_key(anthropic_shape="anthropic-version" in self.headers)
                return
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": model, "object": "model", "created": 0, "owned_by": "mock"}
                        for model in MODELS
                    ],
                },
            )
        else:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/v1/chat/completions", "/chat/completions"):
            self._chat_completions()
        elif path in ("/v1/messages", "/messages"):
            self._messages()
        else:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def _chat_completions(self) -> None:
        if not self._authorized():
            self._reject_key(anthropic_shape=False)
            return
        body = self._read_body()
        if body is None:
            self._send_json(
                400, {"error": {"message": "body must be a JSON object", "type": "invalid_request"}}
            )
            return
        if not body.get("stream"):
            self._send_json(200, openai_completion(body))
            return
        self._start_stream()
        for event in openai_stream_events(body):
            data = event if isinstance(event, str) else json.dumps(event)
            self._write(f"data: {data}\n\n")

    def _messages(self) -> None:
        if not self._authorized():
            self._reject_key(anthropic_shape=True)
            return
        body = self._read_body()
        if body is None:
            self._send_json(
                400,
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "body must be a JSON object",
                    },
                },
            )
            return
        if not body.get("stream"):
            self._send_json(200, anthropic_message(body))
            return
        self._start_stream()
        for name, payload in anthropic_stream_events(body):
            self._write(f"event: {name}\ndata: {json.dumps(payload)}\n\n")


def make_server(port: int = MOCK_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """A server bound to ``host:port``; the caller runs ``serve_forever`` and ``shutdown``."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=MOCK_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = make_server(args.port, args.host)
    print(f"mock provider listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
