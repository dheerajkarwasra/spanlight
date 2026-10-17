"""Anthropic Admin API results → reconciliation rows (spec §4.1, §4.2, §7.3). Pure: no I/O.

A cost-report result carries `amount` as a decimal string in cents, so it is read as text and
divided by 100 as a `Decimal`. A usage-report result carries the token counts behind a cost line
and the priority-tier usage the cost report omits. A result whose `workspace_id` is outside the
source's scope is skipped (`None`); the default workspace is reported as null and named
`default` in a source.

An unknown `token_type` fails the run, because guessing it would put money under the wrong
token key, while an unknown `cost_type` maps to `other`, which is still where the charge belongs.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.pricing.tiers import normalize_tier
from app.reconciliation.line_items import (
    AMOUNT_LIMIT,
    InvalidLineError,
    build_line,
    check_usd,
    count_field,
    mapping_field,
    optional_text,
    quantize_amount,
    scope_allowed,
    text_field,
)
from app.reconciliation.types import (
    MAX_DESCRIPTION_LENGTH,
    MAX_DIMENSION_LENGTH,
    MAX_MODEL_LENGTH,
    MAX_PROVIDER_SCOPE_LENGTH,
    LineKind,
    MappedLine,
    TokenType,
    UsageBucketResult,
)

_TOKEN_TYPES: dict[str, TokenType] = {
    "uncached_input_tokens": "uncached_input",
    "cache_read_input_tokens": "cache_read",
    "cache_creation.ephemeral_5m_input_tokens": "cache_write_5m",
    "cache_creation.ephemeral_1h_input_tokens": "cache_write_1h",
    "output_tokens": "output",
}
_LINE_KINDS: dict[str, LineKind] = {
    "tokens": "tokens",
    "web_search": "web_search",
    "code_execution": "code_execution",
    "session_usage": "other",
}


def map_anthropic_cost_result(
    bucket_day: date,
    result: Mapping[str, Any],
    provider_scope_filter: str | None,
    patterns: Sequence[str],
) -> MappedLine | None:
    """One result of `GET /v1/organizations/cost_report` as a line, or `None` if filtered."""
    check_usd(result.get("currency"), "currency")
    amount = _amount(result.get("amount"))
    workspace = optional_text(result.get("workspace_id"), "workspace_id", MAX_PROVIDER_SCOPE_LENGTH)
    if not scope_allowed(workspace, provider_scope_filter):
        return None
    tier_raw = optional_text(result.get("service_tier"), "service_tier", MAX_DIMENSION_LENGTH)
    context_window = optional_text(
        result.get("context_window"), "context_window", MAX_DIMENSION_LENGTH
    )
    # Only set when grouping by description (§4.1); the column is NOT NULL, so null reads as "".
    description = optional_text(result.get("description"), "description", MAX_DESCRIPTION_LENGTH)
    return build_line(
        day=bucket_day,
        provider="anthropic",
        line_kind=_line_kind(result.get("cost_type"), amount),
        model_raw=optional_text(result.get("model"), "model", MAX_MODEL_LENGTH),
        token_type=_token_type(result.get("token_type")),
        service_tier=normalize_tier("anthropic", tier_raw),
        context_window=context_window,
        provider_scope=workspace,
        description=description or "",
        quantity=None,
        amount_usd=amount,
        patterns=patterns,
    )


def map_anthropic_usage_result(
    bucket_day: date, result: Mapping[str, Any], provider_scope_filter: str | None
) -> UsageBucketResult | None:
    """One result of `GET /v1/organizations/usage_report/messages`, or `None` if filtered.

    A count the report leaves out is `None`, never 0.
    """
    workspace = text_field(result.get("workspace_id"))
    if not scope_allowed(workspace, provider_scope_filter):
        return None
    cache_creation = mapping_field(result.get("cache_creation"))
    server_tools = mapping_field(result.get("server_tool_use"))
    return UsageBucketResult(
        day=bucket_day,
        provider="anthropic",
        model=text_field(result.get("model")),
        service_tier=normalize_tier("anthropic", text_field(result.get("service_tier"))),
        context_window=text_field(result.get("context_window")),
        provider_scope=workspace,
        uncached_input=count_field(result.get("uncached_input_tokens")),
        cache_read=count_field(result.get("cache_read_input_tokens")),
        cache_write_5m=count_field(cache_creation.get("ephemeral_5m_input_tokens")),
        cache_write_1h=count_field(cache_creation.get("ephemeral_1h_input_tokens")),
        output=count_field(result.get("output_tokens")),
        web_search_requests=count_field(server_tools.get("web_search_requests")),
    )


def _amount(value: object) -> Decimal:
    # A decimal string in cents, per §4.1: read as text so no float is ever involved.
    if not isinstance(value, str):
        raise InvalidLineError("amount must be a decimal string", column="amount")
    try:
        cents = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidLineError("amount is not a decimal", column="amount") from exc
    # Checked before dividing: the division applies the decimal context, so a huge exponent would
    # raise `decimal.Overflow` and a signalling NaN `decimal.InvalidOperation`.
    if not cents.is_finite():
        raise InvalidLineError("amount is not a finite number", column="amount")
    if cents.copy_abs() >= AMOUNT_LIMIT * 100:
        raise InvalidLineError("amount is out of range", column="amount")
    return quantize_amount(cents / 100, "amount")


def _token_type(value: object) -> TokenType | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _TOKEN_TYPES:
        raise InvalidLineError(f"unknown token_type {value!r}", column="token_type")
    return _TOKEN_TYPES[value]


def _line_kind(cost_type: object, amount: Decimal) -> LineKind:
    if amount < 0:
        return "credit"
    if cost_type is None:
        return "other"
    if not isinstance(cost_type, str):
        raise InvalidLineError("cost_type must be a string", column="cost_type")
    return _LINE_KINDS.get(cost_type, "other")
