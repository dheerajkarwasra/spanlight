"""What every provider adapter shares: the request and response types and the header rules.

An adapter turns a gateway request (surface, JSON body, the client's headers, the provider key)
into the provider's HTTP request, and sends it through the upstream client from
`app.gateway.http`. Bodies go upstream as the JSON they arrived as and come back byte for byte.

Header rules, both ways, are allowlists:

- Upstream, only `accept`, `user-agent` and the adapter's own provider headers
  (`openai-beta`, or `anthropic-version` and `anthropic-beta`) are copied from the client;
  `content-type` is always `application/json`, since the body is JSON the gateway serialized.
  The provider key goes in the adapter's auth header. The client's `authorization`,
  `x-api-key`, `cookie`, `x-spanlight-*`, `host`, `content-length` and everything else stay
  behind (and the upstream client keeps no cookie jar, so it adds no `cookie` of its own).
- Back to the client, only `content-type`, `retry-after`, `retry-after-ms`, `x-ratelimit-*`,
  `anthropic-ratelimit-*` and `openai-processing-ms` are kept, and the provider's
  `request-id` or `x-request-id` becomes `x-upstream-request-id`.
"""

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

import httpx

from app.db.models import ProviderKind
from app.gateway.errors import Surface

_RESPONSE_HEADERS = frozenset(
    {"content-type", "retry-after", "retry-after-ms", "openai-processing-ms"}
)
_RESPONSE_HEADER_PREFIXES = ("x-ratelimit-", "anthropic-ratelimit-")
_REQUEST_ID_HEADERS = frozenset({"request-id", "x-request-id"})
UPSTREAM_REQUEST_ID_HEADER = "x-upstream-request-id"


class UnsupportedSurface(ValueError):  # noqa: N818 - reads as the condition, like the crypto errors
    """The adapter's provider does not serve this surface. Routing never picks such a target."""


class InvalidUpstreamBody(ValueError):  # noqa: N818 - reads as the condition, like the crypto errors
    """The request body cannot be sent as strict JSON (`NaN` or an infinite number in it).

    Python's JSON parser accepts `NaN`, `Infinity` and numbers such as `1e400`, which become
    floats no JSON encoder may write. The caller answers `INVALID_REQUEST`.
    """


class CredentialEndpoint(Protocol):
    """The part of a provider credential an adapter reads (a `ProviderCredential` row has it)."""

    @property
    def base_url(self) -> str | None: ...


@dataclass(frozen=True)
class UpstreamRequest:
    """One request to a provider. `headers` carry the provider key, so they stay out of `repr`."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(default=b"", repr=False)


@dataclass(eq=False)
class UpstreamResponse:
    """A provider's answer whose body has not been read yet.

    `headers` are the ones to pass back to the client (see the module docstring). `body` yields
    the response bytes as they arrive, decoded from any content-encoding (the gateway asks for
    none). Reading it to the end releases the connection; otherwise the caller must `aclose()`,
    for example when its own client disconnects mid-stream.
    """

    status: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes] = field(repr=False)
    _response: httpx.Response = field(repr=False)

    async def aclose(self) -> None:
        await self._response.aclose()


class ProviderAdapter(Protocol):
    @property
    def provider(self) -> ProviderKind: ...

    def supports(self, surface: Surface) -> bool:
        """Whether the provider serves `surface` (and so whether a target can be routed to)."""
        ...

    def base_url(self, credential: CredentialEndpoint) -> str:
        """The API base that surface paths are appended to."""
        ...

    def prepare(
        self,
        credential: CredentialEndpoint,
        surface: Surface,
        body: Mapping[str, Any],
        client_headers: Mapping[str, str],
        api_key: str,
    ) -> UpstreamRequest:
        """The provider request for `surface`.

        Raises `UnsupportedSurface` and `InvalidUpstreamBody`.
        """
        ...

    async def send(
        self, http: httpx.AsyncClient, upstream: UpstreamRequest, timeout_s: float
    ) -> UpstreamResponse:
        """Send `upstream` and return as soon as the response headers have arrived."""
        ...

    def check_request(self, credential: CredentialEndpoint, api_key: str) -> UpstreamRequest:
        """The provider's model list for this key: what a credential check asks for."""
        ...


class HttpAdapter:
    """The shared implementation. Subclasses set the class attributes and the auth header."""

    provider: ClassVar[ProviderKind]
    # Path appended to the base URL, per served surface.
    paths: ClassVar[Mapping[Surface, str]]
    # Client headers copied upstream, besides `accept` and `user-agent`.
    provider_headers: ClassVar[frozenset[str]] = frozenset()
    # Sent when the client does not send them.
    default_headers: ClassVar[Mapping[str, str]] = {}

    def supports(self, surface: Surface) -> bool:
        return surface in self.paths

    def base_url(self, credential: CredentialEndpoint) -> str:
        raise NotImplementedError

    def auth_headers(self, api_key: str) -> dict[str, str]:
        raise NotImplementedError

    def prepare(
        self,
        credential: CredentialEndpoint,
        surface: Surface,
        body: Mapping[str, Any],
        client_headers: Mapping[str, str],
        api_key: str,
    ) -> UpstreamRequest:
        path = self.paths.get(surface)
        if path is None:
            raise UnsupportedSurface(f"{self.provider.value} does not serve {surface}")
        forwarded = {"accept", "user-agent", *self.provider_headers}
        headers: dict[str, str] = {}
        for name, value in client_headers.items():
            if name.lower() in forwarded:
                headers.setdefault(name.lower(), value)
        for name, value in self.default_headers.items():
            headers.setdefault(name, value)
        url = f"{self.base_url(credential)}{path}"
        if surface == "models":
            return UpstreamRequest("GET", url, {**headers, **self.auth_headers(api_key)})
        headers["content-type"] = "application/json"
        content = strict_json(body)
        return UpstreamRequest("POST", url, {**headers, **self.auth_headers(api_key)}, content)

    async def send(
        self, http: httpx.AsyncClient, upstream: UpstreamRequest, timeout_s: float
    ) -> UpstreamResponse:
        return await send_upstream(http, upstream, timeout_s)

    def check_request(self, credential: CredentialEndpoint, api_key: str) -> UpstreamRequest:
        return self.prepare(credential, "models", {}, {}, api_key)


def strict_json(body: Mapping[str, Any]) -> bytes:
    """`body` as compact strict JSON in UTF-8; `InvalidUpstreamBody` for a NaN or an infinity.

    A lone surrogate (the client sent a `\\ud800` escape) has no UTF-8 form, so a body holding
    one is written with every non-ASCII character escaped instead, as strict JSON allows.
    """
    try:
        serialized = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except ValueError:
        raise InvalidUpstreamBody("the request body contains NaN or an infinite number") from None
    try:
        return serialized.encode()
    except UnicodeEncodeError:
        return json.dumps(body, allow_nan=False, separators=(",", ":")).encode()


async def send_upstream(
    http: httpx.AsyncClient, upstream: UpstreamRequest, timeout_s: float
) -> UpstreamResponse:
    """Send `upstream`; return once the headers are in, with the body still to read.

    `timeout_s` bounds each phase (connect including DNS, each write, each read, waiting for a
    pooled connection); the caller owns the total budget. A redirect comes back as it is.
    Raises the httpx transport errors and `app.core.egress.BlockedAddress` (which is not one:
    a blocked destination is a configuration error, not a network failure).
    """
    request = http.build_request(
        upstream.method,
        upstream.url,
        headers=dict(upstream.headers),
        content=upstream.body or None,
        timeout=httpx.Timeout(timeout_s),
    )
    response = await http.send(request, stream=True, follow_redirects=False)
    return UpstreamResponse(
        status=response.status_code,
        headers=client_headers(response.headers),
        body=response.aiter_bytes(),
        _response=response,
    )


def client_headers(upstream: httpx.Headers) -> dict[str, str]:
    """The provider's response headers that are passed back to the client, first value wins."""
    kept: dict[str, str] = {}
    for name, value in upstream.multi_items():
        lowered = name.lower()
        if lowered in _REQUEST_ID_HEADERS:
            kept.setdefault(UPSTREAM_REQUEST_ID_HEADER, value)
        elif lowered in _RESPONSE_HEADERS or lowered.startswith(_RESPONSE_HEADER_PREFIXES):
            kept.setdefault(lowered, value)
    return kept
