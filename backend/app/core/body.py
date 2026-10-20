"""Reading a request body with a size bound, for routes that read their body themselves.

Ingestion (`/v1/traces`, `/v1/otlp/traces`) and the gateway (`/gw/v1/*`) take bodies larger
than the dashboard's 1 MiB and parse them on their own, so they read through here. The bound
is checked twice before anything is buffered past it: a declared `Content-Length` over the
limit is refused without reading, and a chunked or lying body is cut off as soon as the running
total passes the limit. A gzip or deflate body is decompressed with the same bound applied to
the decompressed size, which defuses decompression bombs.

The failures are plain exceptions, so each caller answers in its own error format
(problem+json for ingestion, the provider's envelope for the gateway).
"""

import gzip
import zlib

from starlette.requests import ClientDisconnect, Request


class BodyTooLarge(Exception):  # noqa: N818 - reads as the condition, like the egress errors
    """The body, or its decompressed form, is longer than the limit."""


class CompressionRatioTooHigh(BodyTooLarge):
    """The body inflates by more than the allowed ratio: cheap to send, costly to parse."""


class UnsupportedEncoding(Exception):  # noqa: N818 - reads as the condition
    """The body's `Content-Encoding` is neither identity, gzip nor deflate."""

    def __init__(self, encoding: str) -> None:
        super().__init__(encoding)
        self.encoding = encoding


class UndecodableBody(Exception):  # noqa: N818 - reads as the condition
    """The body claims gzip or deflate but does not decompress."""


class ClientDisconnected(Exception):  # noqa: N818 - reads as the condition
    """The client went away before it had sent the whole body."""


# Below this decompressed size the ratio is not checked: parsing a megabyte is cheap anyway.
RATIO_CHECK_FLOOR = 1024 * 1024


async def read_bounded_body(request: Request, limit: int, *, max_ratio: int | None = None) -> bytes:
    """The request body, decompressed, refusing to buffer more than `limit` bytes.

    With `max_ratio`, a compressed body that inflates past `RATIO_CHECK_FLOOR` and by more than
    that ratio is refused (`CompressionRatioTooHigh`): a few kilobytes of gzip must not buy
    seconds of parsing.

    Raises `BodyTooLarge`, `UnsupportedEncoding`, `UndecodableBody` and `ClientDisconnected`.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge

    chunks: list[bytes] = []
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise BodyTooLarge
            chunks.append(chunk)
    except ClientDisconnect as exc:
        raise ClientDisconnected from exc
    body = b"".join(chunks)

    encoding = request.headers.get("content-encoding", "identity").lower()
    if encoding in {"", "identity"}:
        return body
    if encoding in {"gzip", "deflate"}:
        output = _decompress(body, limit, gzip_wrapped=encoding == "gzip")
        if max_ratio and len(output) > RATIO_CHECK_FLOOR and len(output) > max_ratio * len(body):
            raise CompressionRatioTooHigh
        return output
    raise UnsupportedEncoding(encoding)


def _decompress(body: bytes, limit: int, *, gzip_wrapped: bool) -> bytes:
    window_bits = 16 + zlib.MAX_WBITS if gzip_wrapped else zlib.MAX_WBITS
    decompressor = zlib.decompressobj(window_bits)
    try:
        output = decompressor.decompress(body, limit + 1)
    except (zlib.error, gzip.BadGzipFile) as exc:
        raise UndecodableBody from exc
    if len(output) > limit or decompressor.unconsumed_tail:
        raise BodyTooLarge
    if not decompressor.eof:
        # The stream stops before its end marker: a cut body whose prefix may still parse.
        raise UndecodableBody
    return output
