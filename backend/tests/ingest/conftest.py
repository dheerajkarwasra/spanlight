"""Fixtures shared by the ingestion tests."""

from typing import Any

import pytest

from tests.helpers import Browser, Workspace, create_workspace


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)
