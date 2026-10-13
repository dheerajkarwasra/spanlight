"""The demo-traffic tests send the job's upstream calls to the shared `fake_provider`."""

import httpx
import pytest

from app.config import Settings
from app.gateway.http import build_http_client
from app.jobs.tasks import demo_traffic
from tests.gateway_support import FakeProvider, public_resolver


@pytest.fixture
def demo_provider(monkeypatch: pytest.MonkeyPatch, fake_provider: FakeProvider) -> FakeProvider:
    """`fake_provider`, set as the upstream of the demo job's gateway client."""

    def make_http_client(settings: Settings) -> httpx.AsyncClient:
        return build_http_client(settings, transport=fake_provider, resolver=public_resolver)

    monkeypatch.setattr(demo_traffic, "make_http_client", make_http_client)
    return fake_provider
