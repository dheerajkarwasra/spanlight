"""Egress protection: the gateway only ever connects to public addresses.

A provider credential can name its own base URL, and the gateway then makes requests to it from
inside the deployment. Without a check, a base URL of `http://169.254.169.254/` (cloud metadata)
or `http://10.0.0.5:5432/` would turn the gateway into a way into the private network. So every
host is resolved first, and the connection is refused when **any** answer is in a blocked range:
checking only the first answer would let a DNS name with one public and one private record
through on the round-robin turn that picks the private one.

`is_blocked_address` and the range list are pure. `resolve_checked` adds DNS; its resolver is a
parameter, so tests fake DNS instead of depending on it. The default resolver is the operating
system's behind `DnsCache`, which keeps an answer for `DNS_CACHE_TTL_SECONDS` so a burst of new
connections to one provider does not pay a lookup each; the check runs on the cached answers
exactly as on fresh ones, and a lookup that fails is not cached.

`VettedNetworkBackend` applies the check where it cannot be raced: when a connection is opened.
It resolves the host itself, vets every answer and connects to a vetted IP literal, so the
address that was checked is the address that is used. Checking a name and then letting the HTTP
client resolve it again would let a DNS answer change in between (DNS rebinding).
"""

import asyncio
import ipaddress
import socket
import time
import typing
from collections.abc import Awaitable, Callable, Iterable, Sequence

import httpcore

from app.core.thread_ownership import owned_thread

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Resolves a host and port to the addresses a connection could go to.
Resolver = Callable[[str, int], Awaitable[Sequence[str]]]

_BLOCKED_IPV4 = tuple(
    ipaddress.IPv4Network(network)
    for network in (
        "0.0.0.0/8",  # unspecified, "this network"
        "127.0.0.0/8",  # loopback
        "10.0.0.0/8",  # RFC 1918
        "172.16.0.0/12",  # RFC 1918
        "192.168.0.0/16",  # RFC 1918
        "169.254.0.0/16",  # link-local, which includes cloud metadata endpoints
        "100.64.0.0/10",  # carrier-grade NAT
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation
        "198.18.0.0/15",  # benchmarking
        "192.88.99.0/24",  # former 6to4 relay anycast, deprecated (RFC 7526)
        "198.51.100.0/24",  # documentation
        "203.0.113.0/24",  # documentation
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, including the broadcast address
    )
)

_BLOCKED_IPV6_NATIVE = tuple(
    ipaddress.IPv6Network(network)
    for network in (
        "::/128",  # unspecified
        "::1/128",  # loopback
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local, deprecated (RFC 3879) but still routed on some networks
        "fc00::/7",  # unique local
        "ff00::/8",  # multicast
        "100::/64",  # discard-only
        "2001:db8::/32",  # documentation
        # IPv4-compatible addresses (`::a.b.c.d`), deprecated since RFC 4291 and never public.
        # Blocked whole, which also covers the loopback and unspecified forms of each.
        "::/96",
        # Local-use NAT64 (RFC 8215): a site's own NAT64 maps these to IPv4 addresses of its
        # choosing, private ones included. Never public, so blocked whole.
        "64:ff9b:1::/48",
        # Teredo (RFC 4380): tunnels to an IPv4 server and client written (obfuscated) inside
        # the address, so it is not vetted by its IPv4 part. Blocked whole.
        "2001::/32",
    )
)

# An IPv4 address can also be written inside an IPv6 one: IPv4-mapped (`::ffff:a.b.c.d`),
# IPv4-translated (`::ffff:0:a.b.c.d`, SIIT), the NAT64 well-known prefix (`64:ff9b::a.b.c.d`),
# which a NAT64 gateway turns back into IPv4, and 6to4 (`2002:aabb:ccdd::/48`, the IPv4 address
# in the 32 bits after the prefix). Each blocked IPv4 range is blocked in every form too, or
# `::ffff:127.0.0.1` would reach loopback. Each entry is the prefix and how many bits the IPv4
# address sits above the end of the 128-bit address.
_IPV4_EMBEDDINGS = (
    (ipaddress.IPv6Network("::ffff:0:0/96"), 0),
    (ipaddress.IPv6Network("::ffff:0:0:0/96"), 0),
    (ipaddress.IPv6Network("64:ff9b::/96"), 0),
    (ipaddress.IPv6Network("2002::/16"), 80),
)


def _embedded(
    prefix: ipaddress.IPv6Network, shift: int, network: ipaddress.IPv4Network
) -> IPNetwork:
    base = int(prefix.network_address) | (int(network.network_address) << shift)
    return ipaddress.IPv6Network((base, prefix.prefixlen + network.prefixlen))


BLOCKED_NETWORKS: tuple[IPNetwork, ...] = (
    *_BLOCKED_IPV4,
    *_BLOCKED_IPV6_NATIVE,
    *(
        _embedded(prefix, shift, network)
        for prefix, shift in _IPV4_EMBEDDINGS
        for network in _BLOCKED_IPV4
    ),
)

# Refused even when `allow_private` opens the private ranges: link-local, where the cloud
# metadata services live (`169.254.169.254` on AWS, GCP and Azure, `fd00:ec2::254` on AWS over
# IPv6, `100.100.100.200` on Alibaba Cloud). On a cloud host they answer with the instance's
# credentials. A self-hoster who opens their own network for webhooks does not mean to open those.
_ALWAYS_BLOCKED_IPV4 = (
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("100.100.100.200/32"),
)
ALWAYS_BLOCKED_NETWORKS: tuple[IPNetwork, ...] = (
    *_ALWAYS_BLOCKED_IPV4,
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("fd00:ec2::254/128"),
    *(
        _embedded(prefix, shift, network)
        for prefix, shift in _IPV4_EMBEDDINGS
        for network in _ALWAYS_BLOCKED_IPV4
    ),
)


class EgressError(Exception):
    """A host the gateway must not, or cannot, connect to. Messages name the host, never a key."""


class BlockedAddress(EgressError):  # noqa: N818 - reads as the condition, like the crypto errors
    """The host resolves to (or is) an address in a blocked range."""


class InsecureUrl(BlockedAddress):
    """A plain `http://` URL while `GATEWAY_ALLOW_INSECURE_BASE_URLS` is off.

    A `BlockedAddress`, so that a caller which refuses blocked egress refuses this too: in both
    cases the configuration is wrong, and retrying or falling back does not fix it.
    """


class UnresolvableHost(EgressError):  # noqa: N818 - reads as the condition, like the crypto errors
    """The host has no address: the name does not exist, or DNS did not answer (in time)."""


class UnresolvableConnectError(httpcore.ConnectError):
    """The `httpcore.ConnectError` that `VettedNetworkBackend` raises for an unresolvable host.

    httpcore's connection pool re-raises connection errors `from None`, which drops the cause,
    so the reason is carried by the type instead.
    """


def scheme_allowed(scheme: str, *, allow_insecure: bool) -> bool:
    """`https`, or also `http` when `GATEWAY_ALLOW_INSECURE_BASE_URLS` is on. Case-insensitive.

    Applied when a base URL is saved and again on every upstream request.
    """
    scheme = scheme.lower()
    return scheme == "https" or (allow_insecure and scheme == "http")


def default_port(scheme: str) -> int:
    """The port a URL without one connects to."""
    return 443 if scheme.lower() == "https" else 80


def _parse(address: str | IPAddress) -> IPAddress | None:
    """`address` as an IP, or None for a string that is not one.

    An IPv6 zone (`fe80::1%eth0`) is ignored: the zone picks an interface, not the address.
    """
    if not isinstance(address, str):
        return address
    try:
        return ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None


def is_blocked_address(address: str | IPAddress) -> bool:
    """Whether a connection to `address` must be refused. A string that is not an IP is blocked."""
    parsed = _parse(address)
    return parsed is None or any(parsed in network for network in BLOCKED_NETWORKS)


def is_always_blocked(address: str | IPAddress) -> bool:
    """Whether `address` is refused even with `allow_private`: link-local or a cloud metadata
    service, written as IPv4, IPv6 or an IPv4 address embedded in IPv6. A string that is not an
    IP is blocked.
    """
    parsed = _parse(address)
    return parsed is None or any(parsed in network for network in ALWAYS_BLOCKED_NETWORKS)


async def system_resolver(host: str, port: int) -> Sequence[str]:
    """Every A and AAAA answer for `host`, through the operating system's resolver."""
    loop = asyncio.get_running_loop()
    try:
        answers = await owned_thread(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM))
    except (socket.gaierror, UnicodeError) as error:
        raise UnresolvableHost(f"{host} could not be resolved") from error
    return [str(sockaddr[0]) for _family, _type, _proto, _name, sockaddr in answers]


# Shorter than the TTL most provider records carry (60 s and up), so a DNS change is followed
# within half a minute; `getaddrinfo` does not expose the record's own TTL.
DNS_CACHE_TTL_SECONDS = 30.0
DNS_CACHE_MAX_ENTRIES = 1024


class DnsCache:
    """A resolver that keeps each host's answers for `ttl_s`, holding at most `max_entries`.

    Only a successful lookup is cached; an error is raised as it is and asked again next time.
    Hosts are chosen by organizations (base URLs, webhooks), so the size bound keeps the cache
    from growing with them. The egress check still runs on every answer this returns.
    """

    def __init__(
        self,
        lookup: Resolver,
        *,
        ttl_s: float = DNS_CACHE_TTL_SECONDS,
        max_entries: int = DNS_CACHE_MAX_ENTRIES,
    ) -> None:
        self._lookup = lookup
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, Sequence[str]]] = {}

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        now = time.monotonic()
        cached = self._entries.get(host)
        if cached is not None and cached[0] > now:
            return cached[1]
        answers = await self._lookup(host, port)
        self._entries.pop(host, None)  # re-insert at the end, so eviction order is by refresh
        if len(self._entries) >= self._max_entries:
            self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
            if len(self._entries) >= self._max_entries:
                del self._entries[next(iter(self._entries))]  # the oldest insertion
        self._entries[host] = (now + self._ttl_s, answers)
        return answers


cached_system_resolver = DnsCache(system_resolver)


async def resolve_checked(
    host: str,
    port: int,
    *,
    allow_private: bool,
    resolver: Resolver | None = None,
    dns_timeout_s: float | None = None,
) -> list[str]:
    """The addresses `host` resolves to, in the resolver's order and without repeats.

    Raises `UnresolvableHost` when there are none, or when DNS takes longer than
    `dns_timeout_s`, and `BlockedAddress` when any of them is in a blocked range. `allow_private`
    lifts that for every range except `ALWAYS_BLOCKED_NETWORKS`. An IP literal is checked as it
    is, without DNS.
    """
    literal = _ip_literal(host)
    if literal is not None:
        answers: Sequence[str] = [str(literal)]
    else:
        try:
            async with asyncio.timeout(dns_timeout_s):
                answers = await (resolver or cached_system_resolver)(host, port)
        except TimeoutError:
            raise UnresolvableHost(f"{host} did not resolve within {dns_timeout_s} s") from None
    addresses = list(dict.fromkeys(answers))
    if not addresses:
        raise UnresolvableHost(f"{host} could not be resolved")
    if any(is_always_blocked(address) for address in addresses):
        raise BlockedAddress(f"{host} resolves to a link-local or cloud metadata address")
    if not allow_private and any(is_blocked_address(address) for address in addresses):
        raise BlockedAddress(f"{host} resolves to a private, local or reserved address")
    return addresses


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


class VettedNetworkBackend(httpcore.AsyncNetworkBackend):
    """An httpcore network backend that only ever connects to vetted addresses.

    `connect_tcp` resolves the host, refuses the connection when any answer is blocked (unless
    `allow_private`) and connects to the vetted IP literals in the resolver's order until one
    accepts. The host name is not lost on the way: httpcore starts TLS on the returned stream
    with the URL's host as `server_hostname`, so SNI and the certificate check use the name, not
    the IP. DNS and connecting share one deadline, the request's connect timeout.

    Errors: `BlockedAddress` is raised as it is. It is not an httpcore error, so httpx passes it
    through unchanged and no retry logic that catches transport errors mistakes it for a network
    failure. An unresolvable host is an `UnresolvableConnectError` (an `httpcore.ConnectError`)
    and an expired deadline an `httpcore.ConnectTimeout`, which httpx maps to its own types.
    """

    def __init__(
        self,
        *,
        allow_private: bool,
        resolver: Resolver | None = None,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._allow_private = allow_private
        self._resolver = resolver
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        local_address: str | None = None,
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            async with asyncio.timeout(timeout):
                addresses = await resolve_checked(
                    host, port, allow_private=self._allow_private, resolver=self._resolver
                )
                return await self._connect_first(addresses, port, local_address, socket_options)
        except TimeoutError:
            raise httpcore.ConnectTimeout(f"connecting to {host} took over {timeout} s") from None
        except UnresolvableHost as error:
            raise UnresolvableConnectError(str(error)) from error

    async def _connect_first(
        self,
        addresses: Sequence[str],
        port: int,
        local_address: str | None,
        socket_options: Iterable[typing.Any] | None,
    ) -> httpcore.AsyncNetworkStream:
        options = list(socket_options or ())
        failure = httpcore.ConnectError("no address to connect to")
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, local_address=local_address, socket_options=options
                )
            except httpcore.ConnectError as error:
                # A host with an AAAA record but no IPv6 route fails at once; try the next.
                failure = error
        raise failure

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedAddress("a Unix socket is not an allowed destination")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
