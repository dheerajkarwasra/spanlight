"""Single-attempt spend ceilings for the expanded gateway providers."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.db.models import ProviderKind
from app.gateway.provider_billing import NEW_TEXT_PROVIDERS, supports_text_rates
from app.gateway.sse import as_int
from app.pricing.cost import MAX_PRICED_TOKENS, PriceBook

UNBOUNDED_PROVIDER = "Spend cannot be bounded for an eligible provider, model, or configuration"

# Full documented prompt and output windows, independent of the provider's tokenizer or its
# interpretation of request-side output limits. Azure deployment names can refer to any model
# in the Azure OpenAI catalogue, so use the largest published Chat Completions limits.
_AZURE_LIMITS = (1_050_000, 128_000)
_LIMITS: dict[tuple[str, str], tuple[int, int]] = {
    (ProviderKind.MISTRAL, "mistral-small-2603"): (256_000, 256_000),
    (ProviderKind.GROQ, "openai/gpt-oss-20b"): (131_072, 65_536),
    (ProviderKind.GROQ, "openai/gpt-oss-120b"): (131_072, 65_536),
    (ProviderKind.TOGETHER, "Qwen/Qwen3.5-9B"): (262_144, 262_144),
    (ProviderKind.TOGETHER, "openai/gpt-oss-120b"): (131_072, 131_072),
    (ProviderKind.XAI, "grok-4.7"): (500_000, 500_000),
}

# DeepSeek's published weekday peak rates are admission-only. Settlement depends on the
# billing calendar, including public holidays in China.
_DEEPSEEK_PEAK = {
    "deepseek-flash": (Decimal("0.30"), Decimal("1.20")),
    "deepseek-v4-pro": (Decimal("1.32"), Decimal("3.96")),
}
_PEAK_VERIFIED_FROM = datetime(2026, 11, 18, tzinfo=UTC)
_PER_MILLION = Decimal(1_000_000)


def requires_provider_bound(providers: Sequence[str | None]) -> bool:
    return any(provider in NEW_TEXT_PROVIDERS for provider in providers)


def provider_cost_ceiling(
    provider: str,
    model: str,
    body: Mapping[str, Any],
    prices: PriceBook,
    at: datetime,
) -> Decimal | None:
    if not supports_text_rates(body):
        return None
    n = as_int(body.get("n", 1))
    if n is None or n < 1:
        return None
    tier = _reservation_tier(provider, body.get("service_tier"))
    if tier is None:
        return None
    if provider == ProviderKind.DEEPSEEK:
        rates = _DEEPSEEK_PEAK.get(model)
        if at < _PEAK_VERIFIED_FROM or rates is None or n != 1:
            return None
        prompt, output = rates
        return (prompt * 1_000_000 + output * 384_000) / _PER_MILLION

    limits = (
        _AZURE_LIMITS if provider == ProviderKind.AZURE_OPENAI else _LIMITS.get((provider, model))
    )
    if limits is None:
        return None
    prompt_cap, output_cap = limits
    if n * output_cap > MAX_PRICED_TOKENS:
        return None
    return prices.token_cost_ceiling(
        provider, model, at, prompt_cap, n * output_cap, service_tier=tier
    )


def _reservation_tier(provider: str, value: object) -> str | None:
    if provider == ProviderKind.MISTRAL:
        if value == "auto":
            return "priority"
        return "standard" if value in (None, "standard_only") else None
    if provider == ProviderKind.XAI:
        if value in ("priority", "fast"):
            return "priority"
        return "standard" if value in (None, "default", "standard") else None
    if provider == ProviderKind.AZURE_OPENAI:
        return "standard" if value in (None, "default", "standard") else None
    return "standard" if value is None else None
