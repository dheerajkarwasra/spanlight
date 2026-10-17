"""CSV rows of the §8.4 upload → `MappedLine`. Pure: no I/O.

Each cell arrives as the text in the file, trimmed here, and is checked against the column rules
of §8.4 and the length limits of §6.7. A bad cell raises `InvalidLineError` naming its column, which
the importer (§7.6) collects as `{row, column, message}`. Amounts are plain ASCII decimals with at
most eight decimals, read as `Decimal` from the text, so nothing is rounded.

A day must lie within the `PROVIDER_LINES_RETENTION_DAYS` before the upload's UTC date, that date
included. An older line would be deleted by retention, a later one has not been billed, and either
end would open a run window (and a backfill per month of it) across centuries.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import cast, get_args

from app.db.models.billing import BillingProvider
from app.pricing.tiers import SERVICE_TIERS, ServiceTier
from app.reconciliation.line_items import InvalidLineError, build_line, check_amount_range
from app.reconciliation.retention import PROVIDER_LINES_RETENTION_DAYS
from app.reconciliation.types import (
    LINE_KINDS,
    MAX_DESCRIPTION_LENGTH,
    MAX_MODEL_LENGTH,
    MAX_PROVIDER_SCOPE_LENGTH,
    MAX_QUANTITY,
    TOKEN_TYPES,
    CsvRow,
    LineKind,
    MappedLine,
    TokenType,
)

# The providers a CSV upload may name (§8.4): the same set as `billing_sources.provider`.
CSV_PROVIDERS: frozenset[str] = frozenset(get_args(BillingProvider))
# `quantity` is a `bigint`; 19 digits is the longest text that can hold one.
MAX_QUANTITY_DIGITS = len(str(MAX_QUANTITY))
# `Decimal()` also reads exponents, underscores, NaN and other scripts' digits; §8.4 does not.
_AMOUNT_TEXT = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_MAX_AMOUNT_DECIMALS = 8


def map_csv_row(row: CsvRow, patterns: Sequence[str], *, today: date) -> MappedLine:
    """One data row of the §8.4 upload as a line; `InvalidLineError` names the bad column.

    `today` is the upload's UTC date: the row's day must lie in the retention window ending on it.
    """
    # A TypedDict iterates as `object` values; every §8.4 cell is text.
    cells = {column: value.strip() for column, value in cast(Mapping[str, str], row).items()}
    line_kind = cast(LineKind, _csv_choice(cells["line_kind"], "line_kind", LINE_KINDS))
    model_raw = _csv_optional(cells["model"], "model", MAX_MODEL_LENGTH)
    token_type = cast(
        TokenType | None, _csv_optional_choice(cells["token_type"], "token_type", TOKEN_TYPES)
    )
    service_tier = cast(
        ServiceTier | None,
        _csv_optional_choice(cells["service_tier"], "service_tier", SERVICE_TIERS),
    )
    description = _csv_optional(cells["description"], "description", MAX_DESCRIPTION_LENGTH)
    if description is None:
        description = " ".join(part for part in (model_raw, token_type) if part)
    return build_line(
        day=_csv_day(cells["day"], today),
        provider=_csv_choice(cells["provider"], "provider", CSV_PROVIDERS),
        line_kind=line_kind,
        model_raw=model_raw,
        token_type=token_type,
        service_tier=service_tier,
        context_window=None,
        provider_scope=_csv_optional(
            cells["provider_scope"], "provider_scope", MAX_PROVIDER_SCOPE_LENGTH
        ),
        description=description,
        quantity=_csv_quantity(cells["quantity"]),
        amount_usd=_csv_amount(cells["amount_usd"], line_kind),
        patterns=patterns,
    )


def _csv_optional(cell: str, column: str, limit: int) -> str | None:
    if not cell:
        return None
    if len(cell) > limit:
        raise InvalidLineError(f"{column} is longer than {limit} characters", column=column)
    return cell


def _csv_choice(cell: str, column: str, allowed: frozenset[str]) -> str:
    if cell not in allowed:
        raise InvalidLineError(
            f"{column} must be one of {', '.join(sorted(allowed))}", column=column
        )
    return cell


def _csv_optional_choice(cell: str, column: str, allowed: frozenset[str]) -> str | None:
    if not cell:
        return None
    return _csv_choice(cell, column, allowed)


def _csv_day(cell: str, today: date) -> date:
    # `date.fromisoformat` also accepts `YYYYMMDD`; the format is pinned to `YYYY-MM-DD`.
    if len(cell) != 10 or cell[4] != "-" or cell[7] != "-":
        raise InvalidLineError("day must be YYYY-MM-DD", column="day")
    try:
        day = date.fromisoformat(cell)
    except ValueError as exc:
        raise InvalidLineError("day is not a valid date", column="day") from exc
    if not today - timedelta(days=PROVIDER_LINES_RETENTION_DAYS) <= day <= today:
        raise InvalidLineError(
            f"day must be within the {PROVIDER_LINES_RETENTION_DAYS} days up to today (UTC)",
            column="day",
        )
    return day


def _csv_amount(cell: str, line_kind: LineKind) -> Decimal:
    if _AMOUNT_TEXT.fullmatch(cell) is None:
        raise InvalidLineError("amount_usd is not a plain decimal number", column="amount_usd")
    _, _, decimals = cell.partition(".")
    if len(decimals) > _MAX_AMOUNT_DECIMALS:
        raise InvalidLineError("amount_usd has more than 8 decimals", column="amount_usd")
    amount = Decimal(cell)
    check_amount_range(amount, "amount_usd")
    if amount < 0 and line_kind != "credit":
        raise InvalidLineError(
            "amount_usd must be >= 0 unless line_kind is credit", column="amount_usd"
        )
    return amount


def _csv_quantity(cell: str) -> int | None:
    if not cell:
        return None
    # `str.isdigit` also accepts superscripts and other scripts' digits, which `int` rejects or
    # reads differently; the length cap keeps `int` away from its digit limit.
    if not (cell.isascii() and cell.isdigit()):
        raise InvalidLineError("quantity must be a non-negative integer", column="quantity")
    if len(cell) > MAX_QUANTITY_DIGITS or int(cell) > MAX_QUANTITY:
        raise InvalidLineError("quantity is too large", column="quantity")
    return int(cell)
