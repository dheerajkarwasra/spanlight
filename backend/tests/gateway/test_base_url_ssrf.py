"""A provider credential's base URL can never point the gateway into the private network.

`validate_base_url` refuses URLs whose host could be read two ways (non-canonical IP forms,
userinfo); `check_base_url_host` resolves the host and refuses private, loopback, link-local and
reserved addresses, IP literals included, without asking DNS for a literal. DNS is faked.
"""

from collections.abc import Sequence

import pytest

from app.core.egress import BlockedAddress
from app.gateway.credentials import InvalidBaseUrl, check_base_url_host, validate_base_url


class FakeResolver:
    def __init__(self, answers: Sequence[str]) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        self.asked.append(host)
        return self.answers


@pytest.mark.parametrize(
    "url",
    [
        "https://0177.0.0.1/v1",  # octal loopback
        "https://127.1/v1",  # short-form loopback
        "https://2130706433/v1",  # integer loopback
        "https://0x7f.0.0.1/v1",  # hex loopback
        "https://169.254.43518/v1",  # short-form link-local
        "https://user@api.example.com/v1",
        "https://@api.example.com/v1",
        "http://api.example.com/v1",
    ],
)
def test_a_base_url_whose_host_could_be_read_two_ways_is_refused(url: str) -> None:
    with pytest.raises(InvalidBaseUrl):
        validate_base_url(url, allow_insecure=False)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/v1",
        "https://10.0.0.5:5432/v1",
        "https://192.168.1.1/v1",
        "https://169.254.169.254/latest",
        "https://[::1]/v1",
        "https://[fe80::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://[::ffff:169.254.169.254]/v1",
    ],
)
async def test_a_private_or_reserved_ip_literal_is_blocked_without_dns(url: str) -> None:
    resolver = FakeResolver(["93.184.216.34"])
    validated = validate_base_url(url, allow_insecure=False)

    with pytest.raises(BlockedAddress):
        await check_base_url_host(validated, allow_insecure=False, resolver=resolver)
    assert resolver.asked == []


@pytest.mark.parametrize("answers", [["10.0.0.5"], ["93.184.216.34", "169.254.169.254"]])
async def test_a_name_with_any_private_answer_is_blocked(answers: list[str]) -> None:
    url = validate_base_url("https://llm.example.com/v1", allow_insecure=False)

    with pytest.raises(BlockedAddress):
        await check_base_url_host(url, allow_insecure=False, resolver=FakeResolver(answers))


async def test_a_public_name_is_accepted() -> None:
    url = validate_base_url("https://llm.example.com/v1/", allow_insecure=False)

    assert url == "https://llm.example.com/v1"
    await check_base_url_host(url, allow_insecure=False, resolver=FakeResolver(["93.184.216.34"]))


async def test_allow_insecure_lets_a_private_host_through() -> None:
    url = validate_base_url("http://10.0.0.5:8000/v1", allow_insecure=True)

    await check_base_url_host(url, allow_insecure=True, resolver=FakeResolver(["10.0.0.5"]))
