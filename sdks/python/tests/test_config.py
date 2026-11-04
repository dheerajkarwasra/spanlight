"""Key hygiene: the API key never shows in a repr and never travels over plain http."""

from __future__ import annotations

import logging

import httpx
import pytest

import spanlight

KEY = "spl_live_testprefix_secretvalue"


def test_config_repr_and_str_hide_the_api_key():
    client = spanlight.Spanlight(api_key=KEY, host="https://collector.example.com", enabled=False)

    assert KEY not in repr(client.config)
    assert KEY not in str(client.config)
    assert client.config.api_key == KEY


def test_plain_http_to_a_remote_host_disables_export_and_prompt_fetching(caplog):
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(500))

    with caplog.at_level(logging.WARNING, logger="spanlight"):
        client = spanlight.Spanlight(
            api_key=KEY, host="http://collector.example.com", transport=transport
        )
    try:
        assert not client.enabled
        assert "https://" in caplog.text
        assert KEY not in caplog.text
        with client.span("work"):
            pass
        client.prompts.get("greet", fallback="hi")
        client.flush()
        assert requests == []
    finally:
        client.shutdown(timeout=1)


def test_plain_http_host_from_the_environment_is_refused(monkeypatch):
    monkeypatch.setenv("SPANLIGHT_HOST", "http://collector.example.com")
    client = spanlight.Spanlight(api_key=KEY)
    try:
        assert not client.enabled
    finally:
        client.shutdown(timeout=1)


@pytest.mark.parametrize(
    "host",
    [
        "http://localhost:8000",
        "http://LOCALHOST.",
        "http://127.0.0.1",
        "http://127.0.0.2",
        "http://[::1]:8000",
        "http://api:8000",
        "http://10.0.0.5",
        "http://172.16.4.2",
        "http://192.168.1.10",
        "http://169.254.0.1",
        "http://[fd00::1]",
        "http://[fe80::1]",
        "http://x.internal",
        "http://printer.local",
        "http://app.localhost",
        "https://collector.example.com",
        "HTTPS://collector.example.com/",
    ],
)
def test_https_and_local_http_hosts_are_accepted(host):
    client = spanlight.Spanlight(api_key=KEY, host=host)
    try:
        assert client.enabled
    finally:
        client.shutdown(timeout=1)


@pytest.mark.parametrize(
    "host",
    [
        "http://collector.example.com",
        "http://8.8.8.8",
        "http://134744072",
        "http://0x8080808",
        "http://172.32.0.1",
        "http://[2001:db8::1]",
        "http://evil.local.com",
        "http://localhost@evil.com",
        "http://under_score",
        "ftp://localhost",
        "https://",
        "http://[::1",
    ],
)
def test_remote_plain_http_and_malformed_hosts_are_refused_without_raising(host):
    client = spanlight.init(api_key=KEY, host=host)
    try:
        assert not client.enabled
    finally:
        client.shutdown(timeout=1)
