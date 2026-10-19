"""Price overrides and the price catalogue carry cache-write rates and prompt-length tiers.

An override is one row per tier: the same provider, pattern and start time may repeat with a
different `min_prompt_tokens`, and repeats with the same tier are the existing conflict.
"""

from decimal import Decimal
from typing import Any

import httpx
import pytest

from tests.helpers import Workspace, create_workspace

HAIKU_55_TIER = {
    "provider": "anthropic",
    "model_pattern": "claude-haiku-5-5",
    "input_per_mtok": "0.50",
    "output_per_mtok": "2.50",
    "cached_input_per_mtok": "0.05",
    "cache_write_5m_per_mtok": "0.625",
    "cache_write_1h_per_mtok": "1.00",
    "min_prompt_tokens": 100001,
    "effective_from": "2026-10-15T00:00:00Z",
}


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    return await create_workspace(await browser_factory("owner@example.com"))


def _overrides_url(workspace: Workspace) -> str:
    return f"/api/v1/orgs/{workspace.org_id}/price-overrides"


async def _create(workspace: Workspace, body: dict[str, Any]) -> httpx.Response:
    return await workspace.owner.post(_overrides_url(workspace), json=body)


async def test_an_override_tier_with_write_rates_is_created_and_echoed(
    workspace: Workspace,
) -> None:
    response = await _create(workspace, HAIKU_55_TIER)

    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(body["cache_write_5m_per_mtok"]) == Decimal("0.625")
    assert Decimal(body["cache_write_1h_per_mtok"]) == Decimal("1.00")
    assert body["min_prompt_tokens"] == 100001
    assert Decimal(body["cached_input_per_mtok"]) == Decimal("0.05")

    listed = await workspace.owner.get(_overrides_url(workspace))
    assert listed.status_code == 200, listed.text
    [row] = listed.json()
    assert row["id"] == body["id"]
    assert row["min_prompt_tokens"] == 100001
    assert Decimal(row["cache_write_5m_per_mtok"]) == Decimal("0.625")
    assert Decimal(row["cache_write_1h_per_mtok"]) == Decimal("1.00")


async def test_write_rates_and_tier_default_to_null_and_the_base_tier(
    workspace: Workspace,
) -> None:
    response = await _create(
        workspace,
        {
            "provider": "openai",
            "model_pattern": "gpt-4o",
            "input_per_mtok": "1",
            "output_per_mtok": "2",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["cache_write_5m_per_mtok"] is None
    assert body["cache_write_1h_per_mtok"] is None
    assert body["min_prompt_tokens"] == 0


async def test_the_same_tier_twice_is_a_conflict_but_another_tier_is_not(
    workspace: Workspace,
) -> None:
    assert (await _create(workspace, HAIKU_55_TIER)).status_code == 201

    duplicate = await _create(workspace, HAIKU_55_TIER)
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "PRICE_OVERRIDE_EXISTS"

    base_tier = await _create(workspace, {**HAIKU_55_TIER, "min_prompt_tokens": 0})
    assert base_tier.status_code == 201, base_tier.text

    listed = await workspace.owner.get(_overrides_url(workspace))
    assert [row["min_prompt_tokens"] for row in listed.json()] == [0, 100001]


@pytest.mark.parametrize(
    "invalid",
    [
        {"min_prompt_tokens": -1},
        {"min_prompt_tokens": "many"},
        {"cache_write_5m_per_mtok": "-0.01"},
        {"cache_write_1h_per_mtok": "1000000"},
    ],
)
async def test_invalid_tier_fields_are_422(workspace: Workspace, invalid: dict[str, Any]) -> None:
    response = await _create(workspace, {**HAIKU_55_TIER, **invalid})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_the_price_catalogue_lists_the_haiku_55_tier_row(workspace: Workspace) -> None:
    response = await workspace.owner.get("/api/v1/prices")

    assert response.status_code == 200, response.text
    rows = response.json()
    haiku = [
        row
        for row in rows
        if row["provider"] == "anthropic" and row["model_pattern"] == "claude-haiku-5-5"
    ]
    # The 2026-10-01 snapshot knew no write rates and no tiers; its rows say so with nulls and 0.
    frozen = [row for row in haiku if row["version"] == "2026-10-01"]
    assert [
        (row["cache_write_5m_per_mtok"], row["cache_write_1h_per_mtok"], row["min_prompt_tokens"])
        for row in frozen
    ] == [(None, None, 0)]
    # The 2026-10-15 snapshot is the first with write rates and the long-prompt tier.
    first_tiered = [row for row in haiku if row["version"] == "2026-10-15"]
    tiers = {row["min_prompt_tokens"]: row for row in first_tiered}
    assert set(tiers) == {0, 100001}
    tier = tiers[100001]
    assert Decimal(tier["input_per_mtok"]) == Decimal("0.50")
    assert Decimal(tier["cached_input_per_mtok"]) == Decimal("0.05")
    assert Decimal(tier["cache_write_5m_per_mtok"]) == Decimal("0.625")
    assert Decimal(tier["cache_write_1h_per_mtok"]) == Decimal("1.00")
    base = tiers[0]
    assert Decimal(base["cache_write_5m_per_mtok"]) == Decimal("0.125")
    assert Decimal(base["cache_write_1h_per_mtok"]) == Decimal("0.20")
    # Rows of one pattern are listed by start time, then base tier first.
    assert [(row["version"], row["min_prompt_tokens"]) for row in haiku] == [
        ("2026-10-01", 0),
        ("2026-10-15", 0),
        ("2026-10-15", 100001),
        ("2026-10-16", 0),
        ("2026-10-16", 100001),
        ("2026-10-19", 0),
        ("2026-10-19", 100001),
    ]
