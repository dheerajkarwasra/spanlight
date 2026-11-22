"""A protected call must reserve the costliest eligible prompt band."""

from datetime import UTC, datetime
from decimal import Decimal

from app.pricing.cost import Price, PriceBook


def test_cost_ceiling_checks_a_costlier_short_prompt_band() -> None:
    now = datetime(2026, 11, 18, tzinfo=UTC)
    model = "gemini-3.5-flash-lite"
    prices = PriceBook(
        [
            Price(
                provider="google",
                model_pattern=model,
                input_per_mtok=Decimal("2"),
                output_per_mtok=Decimal("10"),
                cached_input_per_mtok=None,
                effective_from=now,
                version="override:short",
            ),
            Price(
                provider="google",
                model_pattern=model,
                input_per_mtok=Decimal("1"),
                output_per_mtok=Decimal("1"),
                cached_input_per_mtok=None,
                effective_from=now,
                version="override:long",
                min_prompt_tokens=100_000,
            ),
        ]
    )

    assert prices.token_cost_ceiling("google", model, now, 200_000, 100_000) == Decimal("1.199998")
    assert prices.token_cost_ceiling("google", "unknown", now, 200_000, 100_000) is None
