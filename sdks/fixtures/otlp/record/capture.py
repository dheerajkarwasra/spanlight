"""OTLP capture server for the fixture recorder.

Accepts `POST /v1/otlp/traces`, keeps every export as `(content type, raw body)` in
`CaptureHandle.exports`, optionally writes each one to `--out DIR` as `NNNN.pb` or `NNNN.json`,
and answers `{}`. Point an exporter at it with
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<url>/v1/otlp/traces`.

    python capture.py [--out DIR] [--port 0]

prints the bound port on the first line of stdout and serves until interrupted.
"""

import argparse
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from _server import BodyError, ServerHandle, read_body, run_until_interrupted, start

TRACES_PATH = "/v1/otlp/traces"


@dataclass
class CaptureHandle(ServerHandle):
    exports: list[tuple[str, bytes]] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    exports: list[tuple[str, bytes]]
    out_dir: Path | None
    # Exporters flush batches concurrently; the file number must match the export's position.
    lock = threading.Lock()

    def do_POST(self) -> None:
        try:
            body = read_body(self)
        except BodyError as exc:
            self._reply(exc.status, json.dumps({"error": {"message": exc.message}}).encode())
            return
        if self.path.split("?", 1)[0] != TRACES_PATH:
            self._reply(
                404, b'{"error":{"message":"the capture server only accepts POST /v1/otlp/traces"}}'
            )
            return
        content_type = self.headers.get("content-type", "")
        with self.lock:
            self.exports.append((content_type, body))
            if self.out_dir is not None:
                suffix = ".json" if "json" in content_type else ".pb"
                (self.out_dir / f"{len(self.exports):04d}{suffix}").write_bytes(body)
        self._reply(200, b"{}")

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Quiet: the recorder's stdout carries the port and nothing else."""


def serve(port: int = 0, out_dir: Path | None = None) -> CaptureHandle:
    exports: list[tuple[str, bytes]] = []
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    handler = type(
        "CaptureHandler",
        (_Handler,),
        {"exports": exports, "out_dir": out_dir, "lock": threading.Lock()},
    )
    base = start(handler, port)
    return CaptureHandle(base.url, base.server, base.thread, exports)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    run_until_interrupted(serve(args.port, args.out))
