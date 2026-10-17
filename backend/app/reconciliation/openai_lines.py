"""OpenAI Costs and usage API results → reconciliation rows (spec §4.3, §7.3). Pure: no I/O.

A costs result carries `amount.value` as a JSON number in dollars: its shortest text form is the
figure the provider sent, so it is read through `Decimal(str(value))`, never `Decimal(float)`,
which would expose the binary expansion. `line_item` has no documented grammar; the parser here
covers the shapes the cookbook shows and falls back to `other`. A result whose `project_id` is
outside the source's scope is skipped (`None`).
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.pricing.tiers import ServiceTier
from app.reconciliation.line_items import (
    InvalidLineError,
    build_line,
    check_usd,
    count_field,
    optional_text,
    quantize_amount,
    scope_allowed,
    text_field,
)
from app.reconciliation.types import (
    MAX_DESCRIPTION_LENGTH,
    MAX_MODEL_LENGTH,
    MAX_PROVIDER_SCOPE_LENGTH,
    LineKind,
    MappedLine,
    TokenType,
    UsageBucketResult,
)


def map_openai_cost_result(
    bucket_day: date,
    result: Mapping[str, Any],
    provider_scope_filter: str | None,
    patterns: Sequence[str],
) -> MappedLine | None:
    """One result of `GET /v1/organization/costs` as a line, or `None` if filtered."""
    amount_field = result.get("amount")
    if not isinstance(amount_field, Mapping):
        raise InvalidLineError("amount must be an object", column="amount")
    check_usd(amount_field.get("currency"), "amount.currency")
    amount = _amount(amount_field.get("value"))
    project = optional_text(result.get("project_id"), "project_id", MAX_PROVIDER_SCOPE_LENGTH)
    if not scope_allowed(project, provider_scope_filter):
        return None
    line_item = optional_text(result.get("line_item"), "line_item", MAX_DESCRIPTION_LENGTH) or ""
    model_raw, token_type, line_kind, service_tier = parse_openai_line_item(line_item)
    if amount < 0:
        line_kind = "credit"
    return build_line(
        day=bucket_day,
        provider="openai",
        line_kind=line_kind,
        model_raw=optional_text(model_raw, "line_item", MAX_MODEL_LENGTH),
        token_type=token_type,
        service_tier=service_tier,
        context_window=None,
        provider_scope=project,
        description=line_item,
        quantity=None,
        amount_usd=amount,
        patterns=patterns,
    )


def parse_openai_line_item(
    line_item: str,
) -> tuple[str | None, TokenType | None, LineKind, ServiceTier | None]:
    """`(model_raw, token_type, line_kind, service_tier)` from an OpenAI `line_item` (§7.3).

    The grammar is `[<product> | ]<model>, <class>`. A `batch` product sets the batch tier. A
    class containing `cach` is `cached_input`, then `output`, then `input`; any other class is
    `other` with the model kept. No `, <class>` part means `other`, or `web_search` when the text
    says `web search`. `ft-` stays in the model name.
    """
    parts = [part.strip() for part in line_item.split("|")]
    products, body = parts[:-1], parts[-1]
    service_tier: ServiceTier | None = (
        "batch" if any(p.lower() == "batch" for p in products) else None
    )
    model, separator, token_class = body.rpartition(",")
    if not separator:
        if "web search" in line_item.lower():
            return None, None, "web_search", service_tier
        return None, None, "other", service_tier
    model_raw = model.strip() or None
    token_type = _token_type(token_class)
    if token_type is None:
        return model_raw, None, "other", service_tier
    return model_raw, token_type, "tokens", service_tier


def map_openai_usage_result(
    bucket_day: date, result: Mapping[str, Any], provider_scope_filter: str | None
) -> UsageBucketResult | None:
    """One result of `GET /v1/organization/usage/completions`, or `None` if filtered.

    OpenAI reports total and cached input; the uncached count is their difference. It reports no
    cache writes and no web searches, which stay `None`. `batch: true` is the batch tier; any
    other value is no tier, as a cost line without a `batch |` prefix has none.
    """
    project = text_field(result.get("project_id"))
    if not scope_allowed(project, provider_scope_filter):
        return None
    input_tokens = count_field(result.get("input_tokens"))
    cached = count_field(result.get("input_cached_tokens"))
    uncached = None if input_tokens is None else input_tokens - (cached or 0)
    return UsageBucketResult(
        day=bucket_day,
        provider="openai",
        model=text_field(result.get("model")),
        service_tier="batch" if result.get("batch") is True else None,
        context_window=None,
        provider_scope=project,
        uncached_input=uncached,
        cache_read=cached,
        cache_write_5m=None,
        cache_write_1h=None,
        output=count_field(result.get("output_tokens")),
        web_search_requests=None,
    )


def _amount(value: object) -> Decimal:
    # `Decimal` too: a page parsed with `parse_float=Decimal` keeps the provider's exact text.
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise InvalidLineError("amount.value must be a number", column="amount.value")
    return quantize_amount(Decimal(str(value)), "amount.value")


def _token_type(token_class: str) -> TokenType | None:
    lowered = token_class.strip().lower()
    if "cach" in lowered:
        return "cached_input"
    if "output" in lowered:
        return "output"
    if "input" in lowered:
        return "input"
    return None
