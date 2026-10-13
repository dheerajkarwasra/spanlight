"""The budget tests run the gateway on the pinned `app_clock` (`tests/budget_fixtures.py`)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.config import Settings
from tests.budget_fixtures import app_on_clock
from tests.gateway_support import FakeProvider, ManualClock


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider, app_clock: ManualClock
) -> AsyncIterator[Any]:
    """The shared `gateway_app`, started on `app_clock`."""
    async with app_on_clock(gateway_settings, fake_provider, app_clock) as application:
        yield application
