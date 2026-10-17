"""``normalize_tier``: the SDK's copy of the server's service-tier table.

The spelling cases live in ``sdks/fixtures/service-tier-cases.json``, shared with the backend's
``tests/pricing/test_tiers.py``, so the two tables cannot drift apart unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spanlight._tiers import normalize_tier

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "service-tier-cases.json"
CASES: list[dict[str, str | None]] = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("case", CASES, ids=[f"{c['provider']}:{c['raw']!r}" for c in CASES])
def test_normalize_tier_matches_shared_fixture(case: dict[str, str | None]) -> None:
    assert normalize_tier(case["provider"], case["raw"]) == case["expected"]


def test_fixture_covers_every_tier() -> None:
    assert {case["expected"] for case in CASES} >= {"standard", "batch", "priority", "flex"}


@pytest.mark.parametrize(
    ("provider", "raw", "expected"),
    [
        ("claude", "priority_on_demand", "priority"),
        ("Anthropic", "flex_discount", "flex"),
        ("gemini", "batch", "batch"),
    ],
)
def test_provider_aliases_read_the_same_table(provider: str, raw: str, expected: str) -> None:
    assert normalize_tier(provider, raw) == expected


@pytest.mark.parametrize("provider", ["azure", "azure_openai"])
def test_azure_is_not_an_openai_alias(provider: str) -> None:
    # Azure bills its own rates, so the server does not fold it onto OpenAI; neither does the SDK.
    assert normalize_tier(provider, "fast") is None
    assert normalize_tier(provider, "scale") is None
    assert normalize_tier(provider, "batch") == "batch"


def test_canonical_names_need_no_provider() -> None:
    assert normalize_tier(None, "batch") == "batch"
    assert normalize_tier(None, " Priority ") == "priority"


def test_provider_spellings_need_their_provider() -> None:
    assert normalize_tier(None, "fast") is None
    assert normalize_tier("anthropic", "fast") is None
    assert normalize_tier("unknown-provider", "priority_on_demand") is None


def test_missing_or_blank_raw_is_none() -> None:
    assert normalize_tier("openai", None) is None
    assert normalize_tier("openai", "   ") is None
