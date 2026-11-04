"""The one HTTP client that all upstream (LLM provider) traffic goes through.

`build_http_client` returns an `httpx.AsyncClient` that never follows redirects, sets no default
timeout (each call passes its own) and ignores the environment apart from the CA bundle variables
(`SSL_CERT_FILE`, `SSL_CERT_DIR`): no proxy variables, which would send traffic to a host the
egress check never saw, and no `.netrc`, which could add an `Authorization` header of its own.
It keeps no cookies: the client is shared by every organization, so a cookie one provider set
during one organization's call (Cloudflare sets them on `.openai.com` and `.anthropic.com`)
would otherwise ride on the next organization's calls. It holds a pool of 100 HTTP/1.1
connections, all of which may stay open idle for 90 s: a call after a short idle gap then reuses
a connection instead of paying DNS, TCP and TLS again before its first byte. A connection stays
pinned to the address that was vetted when it was opened, and a provider that closes an idle
connection earlier is noticed by httpcore before the connection is reused.

Two transports sit under it, both in `app.core.egress_http` (shared with the alert channels'
client): `VettedTransport`, the real network through a pool that only connects to vetted
addresses, and `EgressPolicyTransport`, which refuses `http://` unless
`GATEWAY_ALLOW_INSECURE_BASE_URLS` is on and, around an injected test transport, runs the address
check before the request instead.
"""

from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING

import httpx

from app.core.egress import Resolver, UnresolvableConnectError, UnresolvableHost
from app.core.egress_http import EgressPolicyTransport, VettedTransport

if TYPE_CHECKING:
    from app.config import Settings

POOL_SIZE = 100
KEEPALIVE_CONNECTIONS = POOL_SIZE
KEEPALIVE_EXPIRY_SECONDS = 90.0
USER_AGENT = "spanlight-gateway"


def build_http_client(
    settings: "Settings",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> httpx.AsyncClient:
    """The upstream client. Build one per process and close it at shutdown.

    `transport` replaces the network (tests pass an `httpx.MockTransport`); the address check
    still runs, through `resolver` when one is given, so a test fakes DNS and the provider
    together. `resolver` also replaces DNS on the real network.
    """
    allow_insecure = settings.gateway_allow_insecure_base_urls
    inner = (
        transport
        if transport is not None
        else VettedTransport(
            allow_private=allow_insecure,
            resolver=resolver,
            max_connections=POOL_SIZE,
            max_keepalive_connections=KEEPALIVE_CONNECTIONS,
            keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
        )
    )
    policy = EgressPolicyTransport(
        inner,
        allow_insecure=allow_insecure,
        check_addresses=transport is not None,
        resolver=resolver,
    )
    return httpx.AsyncClient(
        transport=policy,
        follow_redirects=False,
        timeout=None,  # noqa: S113 - every call passes its own (see `send_upstream`)
        trust_env=False,
        # A jar that accepts no cookie from any domain, so nothing is ever sent back upstream.
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        # Bodies are forwarded without their content-encoding header, so ask for them unencoded.
        headers={"user-agent": USER_AGENT, "accept-encoding": "identity"},
    )


def host_not_found(error: httpx.TransportError) -> bool:
    """Whether a connect error means the host did not resolve (rather than refused or timed out)."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, UnresolvableHost | UnresolvableConnectError):
            return True
        current = current.__cause__
    return False
