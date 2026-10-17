"""Reading one provider's pages: the request, the status, the capped body, strict JSON (§10.2).

Every request goes through the client from `app.gateway.http.build_http_client` (vetted
addresses, no redirects, no proxies, no cookies) to `base.HOSTS[kind]`. Each page is bounded
three ways, with the limits read from `base` at call time: `PAGE_TIMEOUT_S` for the whole
exchange, `MAX_RESPONSE_BYTES` read before parsing, `MAX_PAGES` per run. Every failure is logged
as `billing_fetch_failed` with the endpoint and status line, never a body or a header.
"""

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr

from app.core.egress import BlockedAddress
from app.core.text import clean_json
from app.db.models.billing import BillingSourceKind
from app.gateway.credential_check import status_line
from app.gateway.http import host_not_found
from app.ingest.jsondepth import exceeds_depth
from app.reconciliation.fetchers import base
from app.reconciliation.fetchers.base import (
    FetchFailed,
    Query,
    invalid_response,
    validated,
)
from app.reconciliation.line_items import InvalidLineError

logger = structlog.get_logger(__name__)


class _Page(BaseModel):
    """The pagination envelope both providers use; the buckets are checked per provider."""

    model_config = ConfigDict(strict=True)

    data: list[dict[str, Any]]
    has_more: StrictBool
    next_page: StrictStr | None = None


@dataclass
class PageReader:
    """Reads JSON pages of one provider for one run, counting them against `MAX_PAGES`."""

    http: httpx.AsyncClient
    kind: BillingSourceKind
    headers: Mapping[str, str]
    timeout_s: float | None = None
    pages: int = field(default=0, init=False)

    async def buckets(self, path: str, query: Query) -> list[dict[str, Any]]:
        """The buckets of every page of `path`, following `next_page` while `has_more`."""
        buckets: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            if self.pages >= base.MAX_PAGES:
                raise self._failed(path, FetchFailed("too many pages", retry="none"))
            self.pages += 1
            page = await self.page(path, [*query, ("page", token)] if token else query)
            buckets.extend(page.data)
            if not page.has_more:
                return buckets
            if page.next_page is None:
                raise self._failed(path, invalid_response("next_page"))
            token = page.next_page

    async def page(self, path: str, query: Query) -> _Page:
        """One page of `path`, read, parsed and checked."""
        try:
            body = await self._get(path, query)
            return parse_page(body)
        except FetchFailed as failure:
            raise self._failed(path, failure) from None

    async def _get(self, path: str, query: Query) -> bytes:
        timeout_s = base.PAGE_TIMEOUT_S if self.timeout_s is None else self.timeout_s
        try:
            async with asyncio.timeout(timeout_s):
                url = base.HOSTS[self.kind] + path
                request = self.http.build_request(
                    "GET", url, params=list(query), headers=self.headers, timeout=timeout_s
                )
                response = await self.http.send(request, stream=True, follow_redirects=False)
                try:
                    check_status(response)
                    return await read_capped(response)
                finally:
                    await response.aclose()
        except (TimeoutError, httpx.TimeoutException):
            raise FetchFailed("timeout", retry="transient") from None
        except httpx.DecodingError:
            raise invalid_response("content-encoding") from None
        except BlockedAddress:
            raise FetchFailed("blocked address", retry="none") from None
        except httpx.LocalProtocolError:
            # A control character in the key: the HTTP layer refuses the header value. The
            # error's text quotes the value, so it is dropped here.
            raise FetchFailed("key cannot be decrypted", retry="none") from None
        except httpx.TransportError as error:
            line = "host not found" if host_not_found(error) else "connection error"
            raise FetchFailed(line, retry="transient") from None
        except UnicodeEncodeError:
            # httpx sends header values as ASCII; the key is the only header taken from a user.
            raise FetchFailed("key cannot be decrypted", retry="none") from None

    def _failed(self, path: str, failure: FetchFailed) -> FetchFailed:
        logger.warning(
            "billing_fetch_failed",
            kind=self.kind,
            endpoint=path,
            page=self.pages,
            status_line=failure.status_line,
            retry=failure.retry,
        )
        return failure


def check_status(response: httpx.Response) -> None:
    """Raise `FetchFailed` with the status line unless the response is a 2xx."""
    code = response.status_code
    if 200 <= code < 300:
        return
    line = status_line(code)
    if code == 429:
        retry_after = retry_after_seconds(response.headers.get("retry-after"))
        raise FetchFailed(line, retry="rate_limited", retry_after_s=retry_after)
    if code >= 500 or code == 408:
        raise FetchFailed(line, retry="transient")
    # 401/403 (the key is wrong until rotated), a redirect (never followed) and other 4xx.
    raise FetchFailed(line, retry="none")


def retry_after_seconds(value: str | None) -> float | None:
    """`Retry-After` as seconds from now (delta seconds or an HTTP date), or None if unusable."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            return None
        seconds = (when - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(seconds, 0.0)


async def read_capped(response: httpx.Response) -> bytes:
    """The raw body, refused past `MAX_RESPONSE_BYTES` before it is all in memory."""
    too_large = FetchFailed("response too large", retry="none")
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > base.MAX_RESPONSE_BYTES:
        raise too_large
    # The client asks for no content-encoding; a compressed answer is refused rather than
    # expanded, so a small body cannot unpack past the cap.
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise invalid_response("content-encoding")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > base.MAX_RESPONSE_BYTES:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def parse_page(body: bytes) -> _Page:
    """The envelope of a page, parsed with the `parse_json_body` rules and amounts as `Decimal`."""
    try:
        return validated(_Page, parse_strict_json(body))
    except InvalidLineError as error:
        raise invalid_response(error.column or "body") from None


def parse_strict_json(body: bytes) -> Any:
    """JSON without NaN or Infinity, at most `MAX_JSON_DEPTH` deep, NUL and lone surrogates
    cleaned (the `app.ingest.pipeline.parse_json_body` rules); numbers with a fraction are
    `Decimal`, so an amount keeps the provider's exact text."""

    def reject_constant(name: str) -> Any:
        raise InvalidLineError(f"{name} is not valid JSON", column="body")

    try:
        parsed = json.loads(body, parse_constant=reject_constant, parse_float=Decimal)
    except (ValueError, RecursionError):  # also a non-UTF-8 body and an int past 4300 digits
        raise InvalidLineError("not valid JSON", column="body") from None
    if exceeds_depth(parsed):
        raise InvalidLineError("nested too deep", column="body")
    return clean_json(parsed)
