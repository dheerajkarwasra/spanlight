"""Provider credentials: the base URL policy, and sealing and opening the API key.

Everything here is pure apart from `core.crypto` and the DNS check in `check_base_url_host`.
The clear API key only passes through `seal_api_key` and `decrypt_api_key`; neither logs it nor
puts it in an exception.
"""

import ipaddress
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from app.core.crypto import Sealed, decrypt, encrypt
from app.core.egress import Resolver, default_port, resolve_checked, scheme_allowed
from app.db.models import ProviderCredential, ProviderKind

if TYPE_CHECKING:
    from app.config import Settings

MAX_BASE_URL_LENGTH = 2048
# How long saving a credential waits for DNS before reporting the host as unresolvable.
RESOLVE_TIMEOUT_SECONDS = 5.0

# A host name: letters, digits, hyphens, underscores and dots, in the ASCII form httpx sends.
_AZURE_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.openai\.azure\.com")
_HOST_NAME = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)*\.?")
# A label that URL parsers and resolvers read as a number: `127`, `0177`, `0x7f`.
_NUMERIC_LABEL = re.compile(r"(0x[0-9a-f]*|[0-9]+)")


class InvalidBaseUrl(ValueError):  # noqa: N818 - reads as the condition, like the crypto errors
    """The base URL breaks one of the rules. The message says which, and is safe to show."""


def validate_base_url(url: str, *, allow_insecure: bool) -> str:
    """The base URL, normalized, or `InvalidBaseUrl`.

    `https://` only (`http://` too when `allow_insecure`), with a host, and without userinfo, a
    query or a fragment: a key must not travel to a host hidden behind `user@`, and the gateway
    appends paths such as `/chat/completions`, which a query or fragment would swallow. Trailing
    slashes are stripped so that appending a path never doubles one.
    """
    url = url.strip()
    if len(url) > MAX_BASE_URL_LENGTH:
        raise InvalidBaseUrl(f"must be at most {MAX_BASE_URL_LENGTH} characters")
    if any(character.isspace() or not character.isprintable() for character in url):
        raise InvalidBaseUrl("must not contain spaces or control characters")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise InvalidBaseUrl("is not a valid URL") from None
    if not scheme_allowed(parts.scheme, allow_insecure=allow_insecure):
        raise InvalidBaseUrl(
            "must start with https:// or http://" if allow_insecure else "must start with https://"
        )
    if "@" in parts.netloc:
        raise InvalidBaseUrl("must not contain a user name or password")
    if parts.query or "?" in url:
        raise InvalidBaseUrl("must not contain a query string")
    if parts.fragment or "#" in url:
        raise InvalidBaseUrl("must not contain a fragment")
    if not parts.hostname:
        raise InvalidBaseUrl("must include a host")
    try:
        _ = parts.port
    except ValueError:
        raise InvalidBaseUrl("has an invalid port") from None
    _check_host(url)
    return url.rstrip("/")


def _check_host(url: str) -> None:
    """Refuse a host that httpx and the egress check could read differently.

    The host is taken from httpx's own parser, the one requests are sent with. An IP address
    must be in its canonical form: `0177.0.0.1`, `127.1` and `2130706433` all mean loopback to
    some resolvers, and are refused rather than interpreted, as is any host whose last label is
    a number (a URL parser treats such a host as an IPv4 address).
    """
    try:
        raw_host = httpx.URL(url).raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeDecodeError):
        raise InvalidBaseUrl("is not a valid URL") from None
    if ":" in raw_host:
        try:
            ipaddress.IPv6Address(raw_host)
        except ValueError:
            raise InvalidBaseUrl("has an invalid IPv6 address") from None
        return
    host = raw_host.lower()
    if not _HOST_NAME.fullmatch(host):
        raise InvalidBaseUrl("has an invalid host name")
    if _NUMERIC_LABEL.fullmatch(host.removesuffix(".").rsplit(".", 1)[-1]):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise InvalidBaseUrl("has an IP address that is not in dotted-decimal form") from None


def azure_base_url(url: str | None) -> str:
    """Validate the resource URL before a saved Azure key can use it."""
    if not url:
        raise InvalidBaseUrl("is required for Azure OpenAI")
    normalized = validate_base_url(url, allow_insecure=False)
    parts = urlsplit(normalized)
    if (
        not _AZURE_HOST.fullmatch(parts.hostname or "")
        or parts.port not in (None, 443)
        or parts.path != "/openai/v1"
    ):
        raise InvalidBaseUrl("must be https://<resource>.openai.azure.com/openai/v1")
    return normalized


def base_url_for(
    provider: ProviderKind, base_url: str | None, *, allow_insecure: bool
) -> str | None:
    """The base URL to store for `provider`: required for Azure and OpenAI-compatible.

    The fixed providers always use their own API host, so a base URL there would be ignored at
    best and, at worst, send OpenAI's key somewhere else.
    """
    if provider is ProviderKind.AZURE_OPENAI:
        return azure_base_url(base_url)
    if provider is ProviderKind.OPENAI_COMPATIBLE:
        if base_url is None or not base_url.strip():
            raise InvalidBaseUrl("is required for an OpenAI-compatible provider")
        return validate_base_url(base_url, allow_insecure=allow_insecure)
    if base_url is not None:
        raise InvalidBaseUrl("is only allowed for Azure OpenAI or an OpenAI-compatible provider")
    return None


def host_and_port(url: str) -> tuple[str, int]:
    """The host and port a validated URL connects to; the scheme's default port when it has none.

    Parsed by httpx, so the host is the one an upstream request connects to (in ASCII form).
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise InvalidBaseUrl("is not a valid URL") from None
    host = parsed.raw_host.decode("ascii")
    if not host:
        raise InvalidBaseUrl("must include a host")
    return host, parsed.port or default_port(parsed.scheme)


async def check_base_url_host(
    url: str, *, allow_insecure: bool, resolver: Resolver | None = None
) -> None:
    """Resolve the base URL's host and refuse private and reserved addresses.

    Raises `BlockedAddress` or `UnresolvableHost` (from `app.core.egress`), the latter also
    when DNS does not answer within `RESOLVE_TIMEOUT_SECONDS`. With `allow_insecure` only an
    unresolvable host is refused. A save-time check only: every upstream connection is checked
    again when it is opened (`app.core.egress.VettedNetworkBackend`).
    """
    host, port = host_and_port(url)
    await resolve_checked(
        host,
        port,
        allow_private=allow_insecure,
        resolver=resolver,
        dns_timeout_s=RESOLVE_TIMEOUT_SECONDS,
    )


def seal_api_key(api_key: str, *, settings: "Settings") -> Sealed:
    """Seal the key under the active key of CREDENTIALS_KEYS (`CryptoNotConfigured` without it)."""
    return encrypt(api_key.encode(), settings=settings)


def decrypt_api_key(credential: ProviderCredential, *, settings: "Settings") -> str:
    """The clear API key. Raises the `core.crypto` errors when it cannot be opened."""
    sealed = Sealed(ciphertext=credential.ciphertext, key_id=credential.key_id)
    return decrypt(sealed, settings=settings).decode()
