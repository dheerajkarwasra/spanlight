"""Which hosts the SDK may send a credential to.

Any https:// host is fine. Plain http:// is allowed only for a host that cannot be on the public
internet: loopback, private and link-local addresses, and local-only names. The TypeScript SDK
applies the same rule (``sdks/typescript/src/client.ts``).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

# Explicit lists rather than ``is_private``, which is broader and would make the SDKs disagree.
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal")
_SINGLE_LABEL = re.compile(r"[a-z0-9-]+")


def is_safe_host(host: str) -> bool:
    """True for an https:// URL, or plain http:// to a local or private-network host."""
    try:
        parts = urlsplit(host)
        hostname = parts.hostname
    except ValueError:  # e.g. an unclosed "[::1"; fail closed instead of raising into the app
        return False
    if not hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and _is_local(hostname)


def _is_local(hostname: str) -> bool:
    name = hostname.removesuffix(".")
    if name == "localhost" or name.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(name)
    except ValueError:
        if "." in name or not _SINGLE_LABEL.fullmatch(name):
            return False
        try:
            # The resolver reads a dotless number ("134744072", "0x8080808") as an IPv4 address.
            address = ipaddress.IPv4Address(socket.inet_aton(name))
        except OSError:
            return True  # a single-label name such as a Compose service ("api")
    return any(address in network for network in _LOCAL_NETWORKS)
