"""Checking a provider credential: list the provider's models with it and report the outcome.

The request is the adapter's `check_request`, sent through the gateway's upstream client, so a
check takes the path every gateway call takes: the same headers, the egress check when the
connection is opened, no redirects. The outcome is a short status line (`401 Unauthorized`,
`timeout`), never the provider's response body: a body can echo request headers, and so the key.
"""

import asyncio
from dataclasses import dataclass
from http import HTTPStatus

import httpx

from app.core.egress import BlockedAddress, InsecureUrl
from app.db.models import ProviderKind
from app.gateway.adapters import adapter_for
from app.gateway.adapters.base import UnsupportedEncoding
from app.gateway.credentials import InvalidBaseUrl, validate_base_url
from app.gateway.http import host_not_found

CHECK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class _Endpoint:
    base_url: str | None


def status_line(status_code: int) -> str:
    """`401 Unauthorized`: the code and its standard phrase, which HTTP/2 responses do not carry."""
    try:
        return f"{status_code} {HTTPStatus(status_code).phrase}"
    except ValueError:
        return str(status_code)


async def probe_models(
    http: httpx.AsyncClient,
    provider: ProviderKind,
    base_url: str | None,
    api_key: str,
    *,
    allow_insecure: bool,
) -> str | None:
    """None when the provider lists its models for this key; otherwise why it did not.

    `http` is the client from `app.gateway.http.build_http_client`. A stored base URL is
    validated again under the current policy first: one saved as `http://` while
    `GATEWAY_ALLOW_INSECURE_BASE_URLS` was on must not receive the key once it is off. The
    whole check, DNS included, takes at most `CHECK_TIMEOUT_SECONDS`. A redirect is reported as
    its status line. `http` vets every address it connects to with the resolver it was built
    with.
    """
    if base_url is not None:
        try:
            validate_base_url(base_url, allow_insecure=allow_insecure)
        except InvalidBaseUrl:
            return "base URL not allowed"
    adapter = adapter_for(provider)
    upstream = adapter.check_request(_Endpoint(base_url), api_key)
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            response = await adapter.send(http, upstream, CHECK_TIMEOUT_SECONDS)
            # Closed unread: only the status matters, and a model list can be large.
            await response.aclose()
            status_code = response.status
    except (TimeoutError, httpx.TimeoutException):
        return "timeout"
    except InsecureUrl:
        return "base URL not allowed"
    except BlockedAddress:
        return "blocked address"
    except UnsupportedEncoding:
        return "unsupported content-encoding"
    except httpx.TransportError as error:
        return "host not found" if host_not_found(error) else "connection error"
    except UnicodeEncodeError:
        # httpx sends header values as ASCII; the key is the only header taken from the user.
        return "key contains characters a provider cannot accept"
    if 200 <= status_code < 300:
        return None
    return status_line(status_code)
