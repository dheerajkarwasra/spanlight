"""`resolve_checked` refuses private, local and reserved destinations (the SSRF guard).

Alert webhooks and gateway base URLs both connect through this check. These tests pin the two
paths a refactor could silently drop: the refusal when any DNS answer is blocked, and the IP
literal path that never asks DNS. DNS is faked through the `resolver` parameter.
"""

import asyncio
from collections.abc import Sequence

import pytest

from app.core.egress import BlockedAddress, UnresolvableHost, resolve_checked


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
