"""Shared plumbing for the harness's two `http.server` based servers."""

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
MAX_BODY_BYTES = 5 * 1024 * 1024


class BodyError(Exception):
    """A request body the server refuses: `status` and a message for the JSON error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Read a POST body, bounded: 400 on a bad Content-Length, 413 above `MAX_BODY_BYTES`."""
    if "chunked" in handler.headers.get("transfer-encoding", "").lower():
        return _read_chunked(handler)
    try:
        length = int(handler.headers.get("content-length", 0))
    except ValueError:
        raise BodyError(400, "Content-Length must be an integer") from None
    if length < 0:
        raise BodyError(400, "Content-Length must not be negative")
    if length > MAX_BODY_BYTES:
        raise BodyError(413, f"request body exceeds {MAX_BODY_BYTES} bytes")
    return handler.rfile.read(length)


def _read_chunked(handler: BaseHTTPRequestHandler) -> bytes:
    """A chunked body (the Node OTLP exporter sends no Content-Length), with the same bound."""
    body = bytearray()
    while True:
        try:
            size = int(handler.rfile.readline().split(b";", 1)[0].strip(), 16)
        except ValueError:
            raise BodyError(400, "malformed chunk size") from None
        if size == 0:
            while handler.rfile.readline() not in (b"\r\n", b"\n", b""):
                pass  # trailers
            return bytes(body)
        if len(body) + size > MAX_BODY_BYTES:
            raise BodyError(413, f"request body exceeds {MAX_BODY_BYTES} bytes")
        body += handler.rfile.read(size)
        handler.rfile.readline()  # the CRLF after each chunk


@dataclass
class ServerHandle:
    """A running server: `url` to point clients at, `close()` when done."""

    url: str
    server: ThreadingHTTPServer = field(repr=False)
    thread: threading.Thread = field(repr=False)

    @property
    def port(self) -> int:
        return self.server.server_port

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def start(handler: type[BaseHTTPRequestHandler], port: int) -> ServerHandle:
    server = ThreadingHTTPServer((HOST, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return ServerHandle(f"http://{HOST}:{server.server_port}", server, thread)


def run_until_interrupted(handle: ServerHandle) -> None:
    """Entry point tail for `python -m <server>`: print the bound port, then block."""
    print(handle.port, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        handle.close()
