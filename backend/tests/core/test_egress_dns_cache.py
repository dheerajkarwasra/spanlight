"""`DnsCache` reuses a DNS answer for a short TTL without weakening the egress check.

Every connection still vets the addresses it will use (cached or fresh), a cached answer is
dropped after the TTL, failures are never cached, the cache is bounded, and the address the
network backend connects to is the one that was checked.
"""

import asyncio
from collections.abc import Sequence

import httpcore
import pytest

from app.core.egress import (
    BlockedAddress,
    DnsCache,
    UnresolvableHost,
    VettedNetworkBackend,
    resolve_checked,
)
from tests.core.test_egress_blocked import FakeResolver, RecordingBackend

PUBLIC = "93.184.216.34"


class FailingResolver:
    def __init__(self) -> None:
        self.asked = 0

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        self.asked += 1
        raise UnresolvableHost(f"{host} could not be resolved")


async def test_a_second_lookup_within_the_ttl_reuses_the_answer() -> None:
    upstream = FakeResolver([PUBLIC])
    cache = DnsCache(upstream, ttl_s=60)

    first = await resolve_checked("llm.example", 443, allow_private=False, resolver=cache)
    upstream.answers = ["10.0.0.1"]
    second = await resolve_checked("llm.example", 443, allow_private=False, resolver=cache)

    assert first == second == [PUBLIC]
    assert upstream.asked == ["llm.example"]


async def test_a_dns_change_is_picked_up_after_the_ttl() -> None:
    upstream = FakeResolver([PUBLIC])
    cache = DnsCache(upstream, ttl_s=0.01)

    await resolve_checked("llm.example", 443, allow_private=False, resolver=cache)
    upstream.answers = ["93.184.216.35"]
    await asyncio.sleep(0.02)

    assert await resolve_checked("llm.example", 443, allow_private=False, resolver=cache) == [
        "93.184.216.35"
    ]
    assert upstream.asked == ["llm.example", "llm.example"]


async def test_a_private_answer_stays_blocked_while_cached() -> None:
    cache = DnsCache(FakeResolver(["10.0.0.1"]), ttl_s=60)

    for _ in range(2):
        with pytest.raises(BlockedAddress):
            await resolve_checked("internal.example", 443, allow_private=False, resolver=cache)


async def test_a_cached_metadata_answer_stays_blocked_with_allow_private() -> None:
    cache = DnsCache(FakeResolver(["10.0.0.5", "169.254.169.254"]), ttl_s=60)

    for _ in range(2):
        with pytest.raises(BlockedAddress):
            await resolve_checked("internal.example", 80, allow_private=True, resolver=cache)


async def test_a_failed_lookup_is_not_cached() -> None:
    upstream = FailingResolver()
    cache = DnsCache(upstream, ttl_s=60)

    for _ in range(2):
        with pytest.raises(UnresolvableHost):
            await resolve_checked("nothing.example", 443, allow_private=False, resolver=cache)
    assert upstream.asked == 2


async def test_the_cache_is_bounded() -> None:
    upstream = FakeResolver([PUBLIC])
    cache = DnsCache(upstream, ttl_s=60, max_entries=2)

    for host in ("a.example", "b.example", "c.example", "a.example"):
        await cache(host, 443)

    assert upstream.asked == ["a.example", "b.example", "c.example", "a.example"]


async def test_the_backend_connects_to_the_checked_address_not_a_newer_one() -> None:
    upstream = FakeResolver([PUBLIC])
    inner = RecordingBackend()
    backend = VettedNetworkBackend(
        allow_private=False, resolver=DnsCache(upstream, ttl_s=60), inner=inner
    )

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("llm.example", 443)
    upstream.answers = ["10.0.0.1"]
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("llm.example", 443)

    assert inner.connected == [PUBLIC, PUBLIC]


async def test_the_backend_refuses_once_a_cached_name_turns_private() -> None:
    upstream = FakeResolver([PUBLIC])
    inner = RecordingBackend()
    backend = VettedNetworkBackend(
        allow_private=False, resolver=DnsCache(upstream, ttl_s=0.01), inner=inner
    )

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("llm.example", 443)
    upstream.answers = ["10.0.0.1"]
    await asyncio.sleep(0.02)

    with pytest.raises(BlockedAddress):
        await backend.connect_tcp("llm.example", 443)
    assert inner.connected == [PUBLIC]
