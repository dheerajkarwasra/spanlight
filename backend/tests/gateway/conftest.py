"""Fixtures for the gateway tests; the shared ones come from `tests/gateway_fixtures.py`."""

import pytest

from app.gateway import stream_forward, upstream_io

SHORT_IDLE_TIMEOUT_S = 0.05


@pytest.fixture
def short_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> float:
    """The stream idle timeout cut to `SHORT_IDLE_TIMEOUT_S`, in every module that reads it."""
    monkeypatch.setattr(upstream_io, "STREAM_IDLE_TIMEOUT_S", SHORT_IDLE_TIMEOUT_S)
    monkeypatch.setattr(stream_forward, "STREAM_IDLE_TIMEOUT_S", SHORT_IDLE_TIMEOUT_S)
    return SHORT_IDLE_TIMEOUT_S
