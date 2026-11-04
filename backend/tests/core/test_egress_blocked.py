"""`resolve_checked` refuses private, local and reserved destinations (the SSRF guard).

Alert webhooks and gateway base URLs both connect through this check. These tests pin the two
paths a refactor could silently drop: the refusal when any DNS answer is blocked, and the IP
literal path that never asks DNS. DNS is faked through the `resolver` parameter.
"""

import asyncio
from collections.abc import Sequence

import httpcore
import pytest

from app.core.egress import (
    BlockedAddress,
    UnresolvableHost,
    VettedNetworkBackend,
    resolve_checked,
)


class FakeResolver:
    """Answers every lookup with `answers` and records the hosts it was asked for."""

    def __init__(self, answers: Sequence[str]) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        self.asked.append(host)
        return self.answers


async def test_a_name_resolving_to_a_private_address_is_blocked() -> None:
    with pytest.raises(BlockedAddress):
        await resolve_checked(
            "internal.example", 443, allow_private=False, resolver=FakeResolver(["10.0.0.1"])
        )


async def test_one_blocked_answer_among_public_ones_is_enough_to_block() -> None:
    resolver = FakeResolver(["93.184.216.34", "127.0.0.1"])

    with pytest.raises(BlockedAddress):
        await resolve_checked("mixed.example", 443, allow_private=False, resolver=resolver)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "[::1]",
        "169.254.169.254",
        "0.0.0.0",  # noqa: S104 - a destination under test, not a bind address
        "[::ffff:169.254.169.254]",
    ],
)
async def test_a_blocked_ip_literal_is_refused_without_dns(host: str) -> None:
    resolver = FakeResolver(["93.184.216.34"])

    with pytest.raises(BlockedAddress):
        await resolve_checked(host, 443, allow_private=False, resolver=resolver)
    assert resolver.asked == []


async def test_a_public_name_resolves_to_its_addresses() -> None:
    resolver = FakeResolver(["93.184.216.34", "93.184.216.34", "2606:2800:220:1::1"])

    addresses = await resolve_checked("example.com", 443, allow_private=False, resolver=resolver)

    assert addresses == ["93.184.216.34", "2606:2800:220:1::1"]


async def test_allow_private_returns_private_addresses() -> None:
    resolver = FakeResolver(["10.0.0.1", "127.0.0.1"])

    addresses = await resolve_checked(
        "internal.example", 443, allow_private=True, resolver=resolver
    )

    assert addresses == ["10.0.0.1", "127.0.0.1"]


async def test_no_answers_is_unresolvable() -> None:
    with pytest.raises(UnresolvableHost):
        await resolve_checked(
            "nothing.example", 443, allow_private=False, resolver=FakeResolver([])
        )


async def test_dns_slower_than_the_timeout_is_unresolvable() -> None:
    async def never_answers(host: str, port: int) -> Sequence[str]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with pytest.raises(UnresolvableHost):
        await resolve_checked(
            "slow.example", 443, allow_private=False, resolver=never_answers, dns_timeout_s=0.01
        )


# `allow_private` opens RFC 1918 and loopback for self-hosters whose webhooks or providers live
# on their own network. It must never open link-local and the cloud metadata addresses: on a
# cloud host those answer with the instance's credentials.
ALWAYS_BLOCKED = [
    "169.254.169.254",
    "::ffff:169.254.169.254",
    "64:ff9b::169.254.169.254",
    "fe80::1",
    "fd00:ec2::254",
    "100.100.100.200",
]


@pytest.mark.parametrize("host", ALWAYS_BLOCKED)
async def test_metadata_and_link_local_literals_stay_blocked_with_allow_private(host: str) -> None:
    with pytest.raises(BlockedAddress):
        await resolve_checked(host, 80, allow_private=True, resolver=FakeResolver([]))


@pytest.mark.parametrize("host", ["10.0.0.5", "127.0.0.1"])
async def test_private_and_loopback_literals_are_allowed_with_allow_private(host: str) -> None:
    assert await resolve_checked(host, 80, allow_private=True, resolver=FakeResolver([])) == [host]


async def test_a_name_with_one_metadata_answer_is_blocked_with_allow_private() -> None:
    resolver = FakeResolver(["10.0.0.5", "169.254.169.254"])

    with pytest.raises(BlockedAddress):
        await resolve_checked("internal.example", 80, allow_private=True, resolver=resolver)


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Records the addresses `VettedNetworkBackend` tried to connect to, and connects to none."""

    def __init__(self) -> None:
        self.connected: list[str] = []

    async def connect_tcp(
        self, host: str, port: int, **kwargs: object
    ) -> httpcore.AsyncNetworkStream:
        self.connected.append(host)
        raise httpcore.ConnectError("recording only")


@pytest.mark.parametrize("host", ALWAYS_BLOCKED)
async def test_the_network_backend_never_connects_to_an_always_blocked_address(host: str) -> None:
    inner = RecordingBackend()
    backend = VettedNetworkBackend(allow_private=True, resolver=FakeResolver([]), inner=inner)

    with pytest.raises(BlockedAddress):
        await backend.connect_tcp(host, 80)
    assert inner.connected == []


async def test_the_network_backend_connects_to_a_private_address_with_allow_private() -> None:
    inner = RecordingBackend()
    backend = VettedNetworkBackend(allow_private=True, resolver=FakeResolver([]), inner=inner)

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("10.0.0.5", 80)
    assert inner.connected == ["10.0.0.5"]
