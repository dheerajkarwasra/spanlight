"""What every provider line shares: identity, pattern mapping, scope, amounts (spec §7.3). Pure.

A `MappedLine` is the `provider_cost_lines` row of §6.7 before the ids. `anthropic_lines`,
`openai_lines` and `csv_rows` build one from their own input shape through `build_line`, and
this module holds the rules they share. Amounts never pass through a float: they are read as
`Decimal` from the provider's text form and quantized once to the column's eight decimals. Only
USD is accepted; any other currency raises `UnsupportedCurrencyError` so the whole run fails and
nothing is written. A malformed result raises `InvalidLineError`.

`line_key` is the identity of a line within a source and day: a replayed fetch upserts the same
row, and a provider correction updates the amount on it.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.core.model_match import matches_model
from app.pricing.tiers import ServiceTier
from app.reconciliation.types import LineKind, MappedLine, TokenType

# `numeric(14, 8)`: the same quantum as a span's cost, rounded once here.
AMOUNT_QUANTUM = Decimal("0.00000001")
# `numeric(14, 8)` keeps six integer digits, so every stored amount is below this in magnitude.
AMOUNT_LIMIT = Decimal("1000000")
# A provider scope spelled `default` stands for the default workspace or project, which the
# provider reports as null.
DEFAULT_SCOPE = "default"


class UnsupportedCurrencyError(ValueError):
    """A line is billed in a currency other than USD; the run must fail without writing."""


class InvalidLineError(ValueError):
    """A result or CSV row does not have the shape the provider or §8.4 promises.

    `column` names the offending field so the CSV importer can report `{row, column, message}`.
    """

    def __init__(self, message: str, *, column: str | None = None) -> None:
        super().__init__(message)
        self.column = column


def line_key(
    provider_scope: str | None,
    description: str,
    token_type: TokenType | None,
    service_tier: ServiceTier | None,
    context_window: str | None,
    model_raw: str | None,
    line_kind: LineKind,
) -> str:
    """SHA-256 of the canonical JSON of the identifying fields, nulls kept (§6.7)."""
    fields = [
        provider_scope,
        description,
        token_type,
        service_tier,
        context_window,
        model_raw,
        line_kind,
    ]
    canonical = json.dumps(fields, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def model_pattern_for(model_raw: str | None, patterns: Sequence[str]) -> str | None:
    """The longest seed or override pattern that `matches_model` the provider's model name."""
    if model_raw is None:
        return None
    matching = [pattern for pattern in patterns if matches_model(pattern, model_raw)]
    if not matching:
        return None
    return max(matching, key=lambda pattern: (len(pattern), pattern))


def scope_allowed(reported: str | None, scope_filter: str | None) -> bool:
    """Whether a line reported under `reported` belongs to a source scoped to `scope_filter`.

    No filter accepts everything; `default` accepts the null scope the provider uses for its
    default workspace or project; anything else must match exactly.
    """
    if scope_filter is None:
        return True
    if reported is None:
        return scope_filter == DEFAULT_SCOPE
    return reported == scope_filter


def build_line(
    *,
    day: date,
    provider: str,
    line_kind: LineKind,
    model_raw: str | None,
    token_type: TokenType | None,
    service_tier: ServiceTier | None,
    context_window: str | None,
    provider_scope: str | None,
    description: str,
    quantity: int | None,
    amount_usd: Decimal,
    patterns: Sequence[str],
) -> MappedLine:
    """A `MappedLine` with its `line_key` and `model_pattern` derived from the other fields."""
    return MappedLine(
        day=day,
        provider=provider,
        line_key=line_key(
            provider_scope,
            description,
            token_type,
            service_tier,
            context_window,
            model_raw,
            line_kind,
        ),
        line_kind=line_kind,
        model_raw=model_raw,
        model_pattern=model_pattern_for(model_raw, patterns),
        token_type=token_type,
        service_tier=service_tier,
        context_window=context_window,
        provider_scope=provider_scope,
        description=description,
        quantity=quantity,
        amount_usd=amount_usd,
    )


def check_usd(currency: object, column: str) -> None:
    """Raise unless `currency` spells USD in any case; a missing currency is an invalid line."""
    if not isinstance(currency, str):
        raise InvalidLineError("currency is missing", column=column)
    if currency.strip().lower() != "usd":
        raise UnsupportedCurrencyError(currency)


def check_amount_range(amount: Decimal, column: str) -> None:
    """Raise unless `amount` fits `numeric(14, 8)`, so a bad value fails here and not at insert."""
    # `copy_abs` ignores the decimal context; `abs()` would overflow on an exponent past Emax.
    if amount.copy_abs() >= AMOUNT_LIMIT:
        raise InvalidLineError(f"{column} is out of range", column=column)


def quantize_amount(amount: Decimal, column: str) -> Decimal:
    """`amount` at eight decimals, rounded half up once; NaN, infinity, out of range are invalid."""
    if not amount.is_finite():
        raise InvalidLineError("amount is not a finite number", column=column)
    check_amount_range(amount, column)
    try:
        quantized = amount.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InvalidLineError(f"{column} is out of range", column=column) from exc
    # Again after rounding: anything from 999999.999999995 rounds up to the limit itself.
    check_amount_range(quantized, column)
    return quantized


def optional_text(value: object, column: str, limit: int) -> str | None:
    """A nullable string field of a cost result, checked against its length limit."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidLineError(f"{column} must be a string", column=column)
    if len(value) > limit:
        raise InvalidLineError(f"{column} is longer than {limit} characters", column=column)
    return value


# Usage-report fields are read leniently: a count or a dimension the report does not carry is
# unknown, not an error, because the usage side only adds counts to lines the cost side owns.


def text_field(value: object) -> str | None:
    return value if isinstance(value, str) else None


def count_field(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def mapping_field(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
