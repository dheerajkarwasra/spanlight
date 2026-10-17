"""Fake OpenAI and Anthropic provider for the OTLP fixture recorder.

Answers `POST /v1/chat/completions`, `/v1/responses`, `/v1/embeddings` and `/v1/messages` from
the bodies under `responses/<provider>/`. The `x-fixture-scenario` request header picks the
body; a request whose JSON body says `"stream": true` gets `<scenario>.sse` as
`text/event-stream`, anything else gets `<scenario>.json`. A scenario named `error_<status>`
answers with that status (and `retry-after: 1` on 429). Any API key is accepted. A scenario
whose body file is absent answers 404 naming the file, so a recipe can never record against a
body the owner has not captured yet.

    python fake_provider.py [--responses DIR] [--port 0]

prints the bound port on the first line of stdout and serves until interrupted.
"""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from _server import BodyError, ServerHandle, read_body, run_until_interrupted, start

RESPONSES_DIR = Path(__file__).resolve().parent / "responses"
SCENARIO_HEADER = "x-fixture-scenario"
# Route -> (provider directory, scenario served when the header is absent).
ROUTES = {
    "/v1/chat/completions": ("openai", "chat"),
    "/v1/responses": ("openai", "responses"),
    "/v1/embeddings": ("openai", "embeddings"),
    "/v1/messages": ("anthropic", "messages"),
}
_SCENARIO = re.compile(r"[a-z0-9_]+")
_ERROR_SCENARIO = re.compile(r"error_(\d{3})")


class _Handler(BaseHTTPRequestHandler):
    responses_dir = RESPONSES_DIR

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        route = ROUTES.get(path)
        try:
            body = read_body(self)
        except BodyError as exc:
            self._error(exc.status, exc.message)
            return
        if route is None:
            self._error(404, f"no route {path}; the fake provider serves {sorted(ROUTES)}")
            return
        provider, default = route
        scenario = self.headers.get(SCENARIO_HEADER, default)
        if not _SCENARIO.fullmatch(scenario):
            self._error(404, f"invalid scenario name {scenario!r}")
            return
        error = _ERROR_SCENARIO.fullmatch(scenario)
        status = int(error.group(1)) if error else 200
        suffix = ".sse" if status == 200 and _wants_stream(body) else ".json"
        file = self.responses_dir / provider / f"{scenario}{suffix}"
        if not file.is_file():
            self._error(
                404,
                f"no body file for scenario {scenario!r}: responses/{provider}/{file.name} is "
                "absent; record it with capture_bodies.py (see README.md)",
            )
            return
        content_type = "text/event-stream" if suffix == ".sse" else "application/json"
        extra = {"retry-after": "1"} if status == 429 else {}
        self._reply(status, content_type, file.read_bytes(), extra)

    def _error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message, "type": "fixture_error"}}).encode()
        self._reply(status, "application/json", body, {})

    def _reply(self, status: int, content_type: str, body: bytes, extra: dict[str, str]) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Quiet: the recorder's stdout carries the port and nothing else."""


def _wants_stream(body: bytes) -> bool:
    try:
        return json.loads(body or b"{}").get("stream") is True
    except (ValueError, AttributeError):
        return False


def serve(responses_dir: Path = RESPONSES_DIR, port: int = 0) -> ServerHandle:
    handler = type("FakeProviderHandler", (_Handler,), {"responses_dir": Path(responses_dir)})
    return start(handler, port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--responses", type=Path, default=RESPONSES_DIR)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    run_until_interrupted(serve(args.responses, args.port))
