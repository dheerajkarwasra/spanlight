"""Pricing, admission and usage for first-class providers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.budgets.reserve import estimate_with_clamp
from app.cli_prices import missing_models, rate_differences
from app.db.models import ProviderKind
from app.gateway.usage import chat_usage
from app.ingest.finish_reason import canonical_finish_reason
from app.pricing.cost import PriceBook
from app.pricing.price_row import Price
from app.pricing.prices import SEED_PRICES, seed_price_book

AT = datetime(2026, 11, 18, tzinfo=UTC)
DEPLOYMENT = "gpt-4o"
BODY = {"messages": [{"role": "user", "content": "Hello"}]}


@pytest.mark.parametrize(
    ("provider", "model", "prompt", "cached", "expected"),
    [
        ("mistral", "mistral-small-2603", 100, 50, "0.00001425"),
        ("groq", "openai/gpt-oss-20b", 100, 50, "0.00000860"),
        ("together", "Qwen/Qwen3.5-9B", 100, 0, "0.00001950"),
        ("xai", "grok-4.7", 200_000, 100_000, "0.50012000"),
    ],
)
def test_verified_rates(provider: str, model: str, prompt: int, cached: int, expected: str) -> None:
    book = seed_price_book()
    kwargs = dict(provider=provider, model=model, input_tokens=prompt, cached_tokens=cached)
    result = book.cost(**kwargs, at=AT, output_tokens=10)
    assert result is not None and result.cost_usd == Decimal(expected)
    assert result.pricing_version == "2026-11-18"
    assert book.cost(**kwargs, at=AT, output_tokens=None) is None
    assert book.cost(**kwargs, at=AT - timedelta(microseconds=1), output_tokens=10) is None


@pytest.mark.parametrize("providers", [["azure_openai"], ["azure_openai", "openai"]])
def test_budget_refuses_any_unpriced_azure_candidate(providers: list[str]) -> None:
    book = seed_price_book()
    models = [DEPLOYMENT] * len(providers)

    assert estimate_with_clamp(
        BODY, DEPLOYMENT, models, providers, book, at=AT, native_gemini=False
    ) == (None, None)
    assert book.find("azure_openai", DEPLOYMENT, AT) is None


def test_azure_estimates_and_settles_using_its_own_deployment_price() -> None:
    book = PriceBook(
        [
            Price("openai", DEPLOYMENT, Decimal("500"), Decimal("500"), None, AT, "fixture"),
            Price(
                "azure_openai", DEPLOYMENT, Decimal("0.25"), Decimal("0.50"), None, AT, "fixture"
            ),
        ]
    )
    estimate, cap = estimate_with_clamp(
        BODY, DEPLOYMENT, [DEPLOYMENT], ["azure_openai"], book, at=AT, native_gemini=False
    )
    assert estimate is not None and Decimal(0) < estimate < Decimal("1")
    assert cap is None
    result = book.cost(
        provider="azure_openai",
        model=DEPLOYMENT,
        at=AT,
        input_tokens=100,
        output_tokens=10,
        cached_tokens=None,
    )
    assert result is not None and result.cost_usd == Decimal("0.00003000")


def test_deepseek_usage_has_explicit_zero_cache_and_scheduled_cost_is_unknown() -> None:
    usage = chat_usage(
        {"prompt_tokens": 10, "completion_tokens": 4, "prompt_cache_hit_tokens": 0},
        provider=ProviderKind.DEEPSEEK,
    )
    assert usage is not None and usage.cached_tokens == 0
    assert (
        seed_price_book().cost(
            provider="deepseek",
            model="deepseek-v4-pro",
            at=AT,
            input_tokens=10,
            output_tokens=4,
            cached_tokens=0,
        )
        is None
    )


def test_cli_keeps_namespaces_and_uses_only_same_provider_entries() -> None:
    feed = {
        "groq/openai/gpt-oss-20b": {
            "litellm_provider": "groq",
            "mode": "chat",
            "input_cost_per_token": 0.000003,
        },
        "together_ai/Org/Model": {
            "litellm_provider": "together_ai",
            "mode": "chat",
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000002,
        },
    }
    differences, _ = rate_differences(SEED_PRICES, feed)
    assert any(line.startswith("groq openai/gpt-oss-20b input_per_mtok:") for line in differences)
    assert '_price("together", "Org/Model", "1", "2", None),' in missing_models([], feed)


def test_xai_response_output_cap_is_length() -> None:
    assert canonical_finish_reason("max_output_tokens") == "length"


def test_azure_deployment_names_do_not_inherit_another_deployments_override() -> None:
    book = PriceBook(
        [Price("azure_openai", DEPLOYMENT, Decimal("1"), Decimal("2"), None, AT, "fixture")]
    )
    assert book.find("azure_openai", DEPLOYMENT + "-2026-11-18", AT) is None
    assert book.find(None, DEPLOYMENT, AT) is None


def test_mistral_model_context_limit_is_length() -> None:
    assert canonical_finish_reason("model_length") == "length"


def test_deepseek_only_uses_peak_rates_to_reserve_spend() -> None:
    book = seed_price_book()
    model = "deepseek-flash"
    bound, clamp = estimate_with_clamp(
        BODY, model, [model], ["deepseek"], book, at=AT, native_gemini=False
    )
    assert bound == Decimal("0.7608")
    assert clamp is None
    assert book.find("deepseek", model, AT) is None


def test_azure_reservation_uses_the_highest_applicable_cache_rate() -> None:
    book = PriceBook(
        [
            Price(
                "azure_openai",
                DEPLOYMENT,
                Decimal("0.25"),
                Decimal("0.50"),
                Decimal("1"),
                AT,
                "fixture",
            ),
        ]
    )
    bound, _ = estimate_with_clamp(
        BODY, DEPLOYMENT, [DEPLOYMENT], ["azure_openai"], book, at=AT, native_gemini=False
    )
    assert bound == Decimal("1.114")


def test_explicit_hosted_tool_configuration_requires_a_bound_even_if_empty() -> None:
    book = seed_price_book()
    body = {**BODY, "search_parameters": {}}
    assert estimate_with_clamp(
        body, "grok-4.7", ["grok-4.7"], ["xai"], book, at=AT, native_gemini=False
    ) == (None, None)
