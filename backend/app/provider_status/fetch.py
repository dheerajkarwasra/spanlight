"""One GET of a status feed: a conditional request, a capped body and a named failure.

`fetch_feed` never raises for anything the network or the feed's server can do: it returns
`Fetched` (a 200 with its body, or a 304 with none) or `Failed` with the poll outcome and the
exception class name. The body is read raw (no decompression) and refused past
`MAX_FEED_BYTES`; the whole request, body included, has `TOTAL_TIMEOUT_SECONDS`, so a server
that drips bytes cannot hold the poll open. Nothing here logs, and no failure carries the body.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from app.core.egress import BlockedAddress
from app.provider_status.outcome import PollOutcome

MAX_FEED_BYTES = 2 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 10.0
# A validator longer than this is not stored: sending a truncated one back would never match.
MAX_VALIDATOR_CHARS = 512


class FeedTooLarge(Exception):  # noqa: N818 - reads as the condition, like `BlockedAddress`
    """The feed's body is over `MAX_FEED_BYTES`."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """A 200 (`body` set) or a 304 (`body` None), with the response's validators."""

    status_code: int
    body: bytes | None
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class Failed:
    """A request that produced no usable body. `error_type` is a class name, never a message."""

    outcome: PollOutcome
    error_type: str
    status_code: int | None = None


async def fetch_feed(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> Fetched | Failed:
    """GET `url`, sending the validators of the last successful fetch when there are any."""
    headers = {}
    if etag is not None:
        headers["if-none-match"] = etag
    if last_modified is not None:
        headers["if-modified-since"] = last_modified
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            return await _get(client, url, headers)
    except BlockedAddress as error:  # an `http://` URL (`InsecureUrl`) is one too
        return Failed(PollOutcome.BLOCKED, type(error).__name__)
    except (TimeoutError, httpx.TimeoutException) as error:
        return Failed(PollOutcome.TIMEOUT, type(error).__name__)
    except httpx.HTTPError as error:
        return Failed(PollOutcome.HTTP_ERROR, type(error).__name__)


async def _get(client: httpx.AsyncClient, url: str, headers: Mapping[str, str]) -> Fetched | Failed:
    async with client.stream("GET", url, headers=headers) as response:
        status = response.status_code
        if status == 304:
            return Fetched(status, None, None, None)
        if status != 200:
            # A redirect lands here too: the pinned URLs are final, so one means a moved feed.
            return Failed(PollOutcome.HTTP_ERROR, httpx.HTTPStatusError.__name__, status)
        try:
            body = await _read_capped(response)
        except FeedTooLarge as error:
            return Failed(PollOutcome.TOO_LARGE, type(error).__name__, status)
        return Fetched(
            status,
            body,
            _validator(response.headers.get("etag")),
            _validator(response.headers.get("last-modified")),
        )


async def _read_capped(response: httpx.Response) -> bytes:
    """The raw body, refused as soon as it is known to be over `MAX_FEED_BYTES`."""
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_FEED_BYTES:
        raise FeedTooLarge
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():
        size += len(chunk)
        if size > MAX_FEED_BYTES:
            raise FeedTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _validator(value: str | None) -> str | None:
    return value if value is not None and len(value) <= MAX_VALIDATOR_CHARS else None
