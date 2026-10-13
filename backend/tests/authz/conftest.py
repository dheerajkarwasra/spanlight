"""Fixtures for the authorization matrices."""

from typing import Any

import pytest

from tests.authz.matrix import Actors
from tests.authz.reference_world import World, build_world
from tests.helpers import Browser, create_workspace, join_with_role


@pytest.fixture
async def matrix_actors(browser_factory: Any) -> Actors:
    """One workspace with an owner, an admin, a member and a viewer, plus an outsider.

    The outsider owns an organization of their own and belongs to none of this one.
    """
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    browsers = {"owner": owner}
    for role in ("admin", "member", "viewer"):
        browser: Browser = await browser_factory(f"{role}@example.com")
        await join_with_role(owner, browser, workspace.org_id, role)
        browsers[role] = browser
    outsider: Browser = await browser_factory("outsider@example.com")
    await create_workspace(outsider, org_name="Other")
    browsers["outsider"] = outsider
    return workspace, browsers


@pytest.fixture
async def world(browser_factory: Any) -> World:
    """Organizations A and B, each owned by one person, with resources A could try to name.

    Needs an `app` that can seal credentials (the gateway app), which the test module provides.
    """
    a: Browser = await browser_factory("a@example.com")
    b: Browser = await browser_factory("b@example.com")
    return await build_world(a, b)
