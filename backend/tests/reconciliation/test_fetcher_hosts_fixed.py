"""Admin keys only ever go to the provider's own host (spec §12 SSRF row; program §4 egress row).

Failure mode: a billing source is pointed at an internal host, by a base URL field or by a
redirect the client follows, and the admin key goes with the request.
"""

import inspect
from collections.abc import Sequence
from datetime import date
from typing import get_args

import pytest

from app.config import Settings
from app.db.models import BillingSourceKind
from app.gateway.http import build_http_client
from app.reconciliation.fetchers import FETCHERS, HOSTS, FetchFailed, fetcher_for, probe
from tests.reconciliation.fetch_support import (
    ANTHROPIC_KEY,
    DAY_FROM,
    DAY_TO,
    OPENAI_KEY,
    PATTERNS,
    Reply,
    ScriptedApi,
    page,
    vetted_client,
)

API_KINDS: list[BillingSourceKind] = ["anthropic_admin_api", "openai_costs_api"]
KEYS = {"anthropic_admin_api": ANTHROPIC_KEY, "openai_costs_api": OPENAI_KEY}


def test_each_api_kind_maps_to_exactly_one_https_host() -> None:
    assert dict(HOSTS) == {
        "anthropic_admin_api": "https://api.anthropic.com",
        "openai_costs_api": "https://api.openai.com",
    }
    # `csv_upload` has no host: it never fetches.
    assert set(FETCHERS) == set(HOSTS) == set(get_args(BillingSourceKind)) - {"csv_upload"}
    with pytest.raises(KeyError):
        fetcher_for("csv_upload")


@pytest.mark.parametrize("kind", API_KINDS)
async def test_requests_go_to_the_constant_host(
    settings: Settings, kind: BillingSourceKind
) -> None:
    api = ScriptedApi(default=Reply(json=page([])))
    async with vetted_client(settings, api) as http:
        await fetcher_for(kind).fetch(http, KEYS[kind], DAY_FROM, DAY_TO, None, PATTERNS)
        await probe(http, kind, KEYS[kind])

    assert len(api.requests) == 3
    for request in api.requests:
        assert f"{request.url.scheme}://{request.url.host}" == HOSTS[kind]
        assert request.url.port is None


@pytest.mark.parametrize("kind", API_KINDS)
def test_no_base_url_parameter_anywhere(kind: BillingSourceKind) -> None:
    signatures: Sequence[inspect.Signature] = [
        inspect.signature(fetcher_for(kind).fetch),
        inspect.signature(probe),
    ]
    for signature in signatures:
        assert not [name for name in signature.parameters if "url" in name or "host" in name]


@pytest.mark.parametrize("kind", API_KINDS)
async def test_redirect_is_reported_not_followed(
    settings: Settings, kind: BillingSourceKind
) -> None:
    redirect = Reply(status=302, json={}, headers={"location": "http://127.0.0.1/steal"})
    api = ScriptedApi(default=redirect)
    async with vetted_client(settings, api) as http:
        with pytest.raises(FetchFailed) as raised:
            await fetcher_for(kind).fetch(http, KEYS[kind], DAY_FROM, DAY_TO, None, PATTERNS)
        assert await probe(http, kind, KEYS[kind], today=date(2026, 10, 3)) == "302 Found"

    assert raised.value.status_line == "302 Found"
    assert raised.value.retry == "none"
    # One request from the fetch and one from the probe; neither followed the redirect.
    assert [request.url.host for request in api.requests] == [HOSTS[kind][8:]] * 2


async def loopback_resolver(host: str, port: int) -> Sequence[str]:
    return ["127.0.0.1"]


@pytest.mark.parametrize("kind", API_KINDS)
async def test_a_host_resolving_to_a_blocked_address_is_refused(
    settings: Settings, kind: BillingSourceKind
) -> None:
    api = ScriptedApi(default=Reply(json=page([])))
    async with vetted_client(settings, api, resolver=loopback_resolver) as http:
        with pytest.raises(FetchFailed) as raised:
            await fetcher_for(kind).fetch(http, KEYS[kind], DAY_FROM, DAY_TO, None, PATTERNS)
        assert await probe(http, kind, KEYS[kind]) == "blocked address"

    assert raised.value.status_line == "blocked address"
    assert raised.value.retry == "none"
    assert api.requests == []


@pytest.mark.parametrize("kind", API_KINDS)
async def test_proxy_settings_in_the_environment_are_ignored(
    settings: Settings, kind: BillingSourceKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy (or a `.netrc`) from the worker's environment would see the admin key in every
    request; the fetch client trusts nothing from the environment."""
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.internal.example:3128")
    async with build_http_client(settings) as http:
        assert http.trust_env is False

    api = ScriptedApi(default=Reply(json=page([])))
    async with vetted_client(settings, api) as http:
        await fetcher_for(kind).fetch(http, KEYS[kind], DAY_FROM, DAY_TO, None, PATTERNS)
    assert {f"{request.url.scheme}://{request.url.host}" for request in api.requests} == {
        HOSTS[kind]
    }
