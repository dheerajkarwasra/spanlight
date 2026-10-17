"""The HTTP client the status poller fetches the providers' feeds with.

The feed URLs are constants, but DNS is not, so the client is locked down like the alert
delivery client (`app.alerts.channels.http`): every connection goes through the egress-vetted
transport (no private, loopback or reserved address, `https` only), redirects are never followed
(the pinned URLs are already the final ones), no proxy or `.netrc` is read from the environment,
no cookie is kept, and every step of a request has 10 seconds. `accept-encoding: identity` asks
for an uncompressed body, which the poller reads raw and caps, so a compressed answer cannot
expand in memory.
"""

from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

from app.core.egress import Resolver
from app.core.egress_http import EgressPolicyTransport, VettedTransport
from app.jobs.worker_heartbeat import backend_version

TIMEOUT_SECONDS = 10.0
# Three feeds polled one after the other: a connection each is plenty.
POOL_SIZE = 3
KEEPALIVE_EXPIRY_SECONDS = 5.0


def build_status_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> httpx.AsyncClient:
    """The feed client. Close it after the poll (`async with`).

    `transport` replaces the network (tests pass an `httpx.MockTransport`); the address check
    then runs before the request, through `resolver` when one is given.
    """
    inner = (
        transport
        if transport is not None
        else VettedTransport(
            allow_private=False,
            resolver=resolver,
            max_connections=POOL_SIZE,
            max_keepalive_connections=POOL_SIZE,
            keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
        )
    )
    policy = EgressPolicyTransport(
        inner,
        allow_insecure=False,
        check_addresses=transport is not None,
        resolver=resolver,
    )
    return httpx.AsyncClient(
        transport=policy,
        follow_redirects=False,
        timeout=TIMEOUT_SECONDS,
        trust_env=False,
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        headers={
            "user-agent": f"spanlight/{backend_version()}",
            "accept-encoding": "identity",
        },
    )
