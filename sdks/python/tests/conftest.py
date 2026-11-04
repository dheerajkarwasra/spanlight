"""Shared fixtures: an in-memory ingestion API and SDK clients wired to it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from support import FakeIngestApi

import spanlight
from spanlight import _globals


@pytest.fixture
def ingest() -> FakeIngestApi:
    return FakeIngestApi()


@pytest.fixture
def tracer(ingest: FakeIngestApi) -> Iterator[spanlight.Spanlight]:
    client = spanlight.Spanlight(
        api_key="spl_live_testprefix_testsecret",
        host="https://spanlight.test",
        environment="test",
        release="v0.0.1",
        flush_interval=0.05,
        transport=ingest.transport(),
    )
    yield client
    client.shutdown(timeout=2)


@pytest.fixture
def global_tracer(ingest: FakeIngestApi) -> Iterator[spanlight.Spanlight]:
    """Install a global client (used by module-level ``observe``/``span``)."""
    client = spanlight.init(
        api_key="spl_live_testprefix_testsecret",
        host="https://spanlight.test",
        environment="test",
        flush_interval=0.05,
        transport=ingest.transport(),
    )
    yield client
    spanlight.shutdown(timeout=2)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "SPANLIGHT_API_KEY",
        "SPANLIGHT_HOST",
        "SPANLIGHT_ENVIRONMENT",
        "SPANLIGHT_RELEASE",
        "SPANLIGHT_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    if _globals._client is not None:
        _globals.shutdown(timeout=1)
