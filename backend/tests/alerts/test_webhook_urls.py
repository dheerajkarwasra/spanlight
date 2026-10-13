"""The save-time rules for a webhook channel's URL (the SSRF guard on alert webhooks).

Pure: DNS is faked through the `resolver` parameter. Every refusal message must describe the
rule without echoing the URL, since the message is shown to the user and logged.
"""

from collections.abc import Sequence

import pytest

from app.alerts.channels.urls import UnsafeUrlError, check_webhook_url, validate_webhook_url


async def _private(host: str, port: int) -> Sequence[str]:
    return ["10.0.0.1"]


async def _public(host: str, port: int) -> Sequence[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/alert",
        "https://user@hooks.example.com/alert",
        "https://user:secret@hooks.example.com/alert",
        "https://@hooks.example.com/alert",
        "https://hooks.example.com/alert#frag",
        "https://hooks.example.com/al\x00ert",
        "https://hooks.example.com/al ert",
        "https:///alert",
        "ftp://hooks.example.com/alert",
    ],
)
def test_an_unsafe_url_is_refused(url: str) -> None:
    with pytest.raises(UnsafeUrlError) as refused:
        validate_webhook_url(url, allow_private=False)

    assert "hooks.example.com" not in str(refused.value)
    assert "secret" not in str(refused.value)


def test_http_is_allowed_only_with_allow_private() -> None:
    assert validate_webhook_url("http://receiver.internal:8080/a", allow_private=True) == (
        "receiver.internal",
        8080,
    )


def test_a_safe_url_gives_its_host_and_default_port() -> None:
    assert validate_webhook_url("https://hooks.example.com/alert", allow_private=False) == (
        "hooks.example.com",
        443,
    )


async def test_a_host_resolving_to_a_private_address_is_refused_without_echoing_it() -> None:
    url = "https://hooks.example.com/alert?token=abc"

    with pytest.raises(UnsafeUrlError) as refused:
        await check_webhook_url(url, allow_private=False, resolver=_private)

    message = str(refused.value)
    assert "private" in message
    assert "hooks.example.com" not in message
    assert "token" not in message
    assert "10.0.0.1" not in message


async def test_a_private_ip_literal_is_refused() -> None:
    with pytest.raises(UnsafeUrlError):
        await check_webhook_url(
            "https://169.254.169.254/latest/meta-data", allow_private=False, resolver=_public
        )


async def test_a_private_host_is_accepted_with_allow_private() -> None:
    await check_webhook_url("https://receiver.internal/a", allow_private=True, resolver=_private)


async def test_a_public_host_is_accepted() -> None:
    await check_webhook_url(
        "https://hooks.example.com/alert", allow_private=False, resolver=_public
    )
