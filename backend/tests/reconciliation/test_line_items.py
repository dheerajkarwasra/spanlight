"""Provider cost lines → `MappedLine` (spec §7.3, §6.7, §8.4). Pure: no database.

Failure mode: a provider amount goes through a float and gains or loses a cent (Review Focus 3),
a non-USD line is silently summed as dollars, a workspace or project outside the source's scope
leaks into the report, or a replayed fetch gets a new `line_key` and doubles every line.

The pages in `fixtures/` are written by hand from spec §4.1-4.3 (two buckets, `has_more`).
"""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.reconciliation.anthropic_lines import map_anthropic_cost_result
from app.reconciliation.csv_rows import map_csv_row
from app.reconciliation.line_items import (
    InvalidLineError,
    UnsupportedCurrencyError,
    line_key,
    model_pattern_for,
)
from app.reconciliation.openai_lines import map_openai_cost_result, parse_openai_line_item
from app.reconciliation.types import CsvRow, MappedLine

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 10, 1)
# The upload's UTC date: a CSV day must lie within the 400 days before it.
TODAY = date(2026, 10, 16)
PATTERNS = ["claude-sonnet-4-5", "claude-haiku-4-5", "gpt-4o", "gpt-4o-mini", "gpt-4.1"]


def load_page(name: str) -> dict[str, Any]:
    page: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return page


def anthropic_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "currency": "USD",
        "amount": "123.45",
        "workspace_id": None,
        "description": "Claude Sonnet 4.5 Usage - Input Tokens",
        "cost_type": "tokens",
        "context_window": "0-200k",
        "model": "claude-sonnet-4-5-20250929",
        "service_tier": "standard",
        "token_type": "uncached_input_tokens",
        "inference_geo": None,
    }
    result.update(overrides)
    return result


def openai_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "object": "organization.costs.result",
        "amount": {"value": 0.1, "currency": "usd"},
        "line_item": "gpt-4o-2024-08-06, input",
        "project_id": "proj_abc123",
    }
    result.update(overrides)
    return result


def csv_row(**overrides: str) -> CsvRow:
    row: CsvRow = {
        "day": "2026-10-01",
        "provider": "anthropic",
        "line_kind": "tokens",
        "amount_usd": "1.2345",
        "model": "claude-sonnet-4-5-20250929",
        "token_type": "uncached_input",
        "service_tier": "standard",
        "quantity": "41150",
        "description": "",
        "provider_scope": "",
    }
    row.update(overrides)  # type: ignore[typeddict-item]  # overrides are column names
    return row


def mapped(result: dict[str, Any], scope: str | None = None) -> MappedLine:
    line = map_anthropic_cost_result(DAY, result, scope, PATTERNS)
    assert line is not None
    return line


# --- Anthropic ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cents", "usd"),
    [
        ("123.45", Decimal("1.2345")),
        ("1", Decimal("0.01")),
        ("0.5", Decimal("0.005")),
        ("250.00", Decimal("2.5")),
        ("0.000001", Decimal("0.00000001")),
    ],
)
def test_anthropic_cents_string_is_exact(cents: str, usd: Decimal) -> None:
    line = mapped(anthropic_result(amount=cents))

    assert isinstance(line.amount_usd, Decimal)
    assert line.amount_usd == usd
    assert line.amount_usd.as_tuple().exponent >= -8


def test_amount_is_rounded_once_to_eight_decimals() -> None:
    # Below the column's quantum the figure rounds half up, as Postgres would round it anyway.
    assert mapped(anthropic_result(amount="0.0000005")).amount_usd == Decimal("0.00000001")
    assert mapped(anthropic_result(amount="0.0000004")).amount_usd == Decimal("0")


def test_anthropic_amount_must_be_a_string() -> None:
    with pytest.raises(InvalidLineError):
        mapped(anthropic_result(amount=123.45))
    with pytest.raises(InvalidLineError):
        mapped(anthropic_result(amount="twelve"))


@pytest.mark.parametrize(
    ("raw", "token_type"),
    [
        ("uncached_input_tokens", "uncached_input"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation.ephemeral_5m_input_tokens", "cache_write_5m"),
        ("cache_creation.ephemeral_1h_input_tokens", "cache_write_1h"),
        ("output_tokens", "output"),
    ],
)
def test_anthropic_every_token_type(raw: str, token_type: str) -> None:
    line = mapped(anthropic_result(token_type=raw))

    assert line.line_kind == "tokens"
    assert line.token_type == token_type
    assert line.model_raw == "claude-sonnet-4-5-20250929"
    assert line.model_pattern == "claude-sonnet-4-5"
    assert line.service_tier == "standard"
    assert line.context_window == "0-200k"


def test_anthropic_unknown_token_type_is_invalid() -> None:
    with pytest.raises(InvalidLineError):
        mapped(anthropic_result(token_type="reasoning_tokens"))


@pytest.mark.parametrize(
    ("cost_type", "amount", "line_kind"),
    [
        ("tokens", "10", "tokens"),
        ("web_search", "10", "web_search"),
        ("code_execution", "2.5", "code_execution"),
        ("session_usage", "0.75", "other"),
        (None, "0.75", "other"),
        ("tokens", "-50", "credit"),
        (None, "-50", "credit"),
    ],
)
def test_anthropic_cost_types(cost_type: str | None, amount: str, line_kind: str) -> None:
    line = mapped(anthropic_result(cost_type=cost_type, amount=amount))

    assert line.line_kind == line_kind
    assert line.amount_usd == Decimal(amount) / 100


def test_anthropic_credit_keeps_its_sign_and_model() -> None:
    line = mapped(anthropic_result(amount="-50"))

    assert line.line_kind == "credit"
    assert line.amount_usd == Decimal("-0.5")
    assert line.model_raw == "claude-sonnet-4-5-20250929"


@pytest.mark.parametrize(
    ("workspace_id", "scope", "kept"),
    [
        (None, None, True),
        (None, "default", True),
        (None, "wrkspc_01ABC", False),
        ("wrkspc_01ABC", "wrkspc_01ABC", True),
        ("wrkspc_01ABC", "default", False),
        ("wrkspc_01ABC", None, True),
        ("wrkspc_01ABC", "wrkspc_01XYZ", False),
    ],
)
def test_anthropic_workspace_filter(
    workspace_id: str | None, scope: str | None, kept: bool
) -> None:
    line = map_anthropic_cost_result(DAY, anthropic_result(workspace_id=workspace_id), scope, [])

    if kept:
        assert line is not None
        assert line.provider_scope == workspace_id
    else:
        assert line is None


@pytest.mark.parametrize("currency", ["EUR", "eur", "GBP"])
def test_anthropic_non_usd_raises(currency: str) -> None:
    with pytest.raises(UnsupportedCurrencyError):
        mapped(anthropic_result(currency=currency))


def test_anthropic_currency_case_insensitive() -> None:
    assert mapped(anthropic_result(currency="usd")).amount_usd == Decimal("1.2345")


def test_anthropic_missing_currency_is_invalid() -> None:
    with pytest.raises(InvalidLineError):
        mapped(anthropic_result(currency=None))


def test_anthropic_fixture_page_maps_every_result() -> None:
    page = load_page("anthropic_cost_page.json")
    assert page["has_more"] is True
    lines: list[MappedLine] = []
    for bucket in page["data"]:
        day = date.fromisoformat(bucket["starting_at"][:10])
        for result in bucket["results"]:
            line = map_anthropic_cost_result(day, result, "default", PATTERNS)
            if line is not None:
                lines.append(line)

    # The workspace line is outside the default workspace and is skipped.
    assert len(lines) == 12
    assert {line.day for line in lines} == {date(2026, 10, 1), date(2026, 10, 2)}
    assert all(line.provider == "anthropic" for line in lines)
    assert [line.line_kind for line in lines].count("credit") == 1
    assert len({line.line_key for line in lines}) == 12 - 2  # two days share ten keys
    batch = [line for line in lines if line.service_tier == "batch"]
    assert [line.context_window for line in batch] == ["200k-1M"]


# --- OpenAI --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "usd"),
    [
        (0.1, Decimal("0.1")),
        (0.0125, Decimal("0.0125")),
        (3, Decimal("3")),
        # Decimal(float) would give 1.00000000499999996961… and round down.
        (1.000000005, Decimal("1.00000001")),
    ],
)
def test_openai_float_value_is_exact(value: float, usd: Decimal) -> None:
    line = map_openai_cost_result(
        DAY, openai_result(amount={"value": value, "currency": "usd"}), None, []
    )

    assert line is not None
    assert isinstance(line.amount_usd, Decimal)
    assert line.amount_usd == usd
    assert line.amount_usd.as_tuple().exponent >= -8


@pytest.mark.parametrize("value", [True, "0.1", None])
def test_openai_amount_must_be_a_number(value: object) -> None:
    with pytest.raises(InvalidLineError):
        map_openai_cost_result(
            DAY, openai_result(amount={"value": value, "currency": "usd"}), None, []
        )


@pytest.mark.parametrize(
    ("value", "usd"),
    [(Decimal("0.1"), Decimal("0.1")), (Decimal("1.000000005"), Decimal("1.00000001"))],
)
def test_openai_decimal_value_is_accepted(value: Decimal, usd: Decimal) -> None:
    # A page parsed with `json.loads(..., parse_float=Decimal)` carries `Decimal` values.
    line = map_openai_cost_result(
        DAY, openai_result(amount={"value": value, "currency": "usd"}), None, []
    )

    assert line is not None
    assert line.amount_usd == usd


@pytest.mark.parametrize("value", [1e21, 1e6, -1e6, Decimal("1E+30")])
def test_openai_amount_out_of_range_is_invalid(value: object) -> None:
    # `numeric(14, 8)` holds |amount| < 1,000,000; 1e21 would also overflow the quantize.
    with pytest.raises(InvalidLineError) as excinfo:
        map_openai_cost_result(
            DAY, openai_result(amount={"value": value, "currency": "usd"}), None, []
        )

    assert excinfo.value.column == "amount.value"
    assert "out of range" in str(excinfo.value)


def test_openai_amount_just_inside_range_is_kept() -> None:
    line = map_openai_cost_result(
        DAY,
        openai_result(amount={"value": Decimal("999999.99999999"), "currency": "usd"}),
        None,
        [],
    )

    assert line is not None
    assert line.amount_usd == Decimal("999999.99999999")


@pytest.mark.parametrize(
    "cents",
    [
        # 100,000,000 cents is $1,000,000, one past what `numeric(14, 8)` holds.
        "100000000",
        # An exponent past the decimal context's Emax would overflow `cents / 100`.
        "1E+999999999",
        "-1E+999999999",
        # Rounds up to 1000000.00000000 at eight decimals.
        "99999999.9999995",
        # A signalling NaN would raise from the division.
        "sNaN",
    ],
)
def test_anthropic_amount_out_of_range_is_invalid(cents: str) -> None:
    with pytest.raises(InvalidLineError) as excinfo:
        map_anthropic_cost_result(DAY, anthropic_result(amount=cents), None, [])

    assert excinfo.value.column == "amount"


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1E+999999999"),
        Decimal("-1E+999999999"),
        Decimal("999999.999999995"),
        999999.999999995,
    ],
)
def test_openai_amount_overflow_or_rounding_past_the_limit_is_invalid(value: object) -> None:
    with pytest.raises(InvalidLineError) as excinfo:
        map_openai_cost_result(
            DAY, openai_result(amount={"value": value, "currency": "usd"}), None, []
        )

    assert excinfo.value.column == "amount.value"
    assert "out of range" in str(excinfo.value)


@pytest.mark.parametrize(
    ("line_item", "expected"),
    [
        ("gpt-4o-2024-08-06, input", ("gpt-4o-2024-08-06", "input", "tokens", None)),
        ("ft-gpt-4o-2024-08-06, output", ("ft-gpt-4o-2024-08-06", "output", "tokens", None)),
        ("assistants api | file search", (None, None, "other", None)),
        (
            "evals | gpt-4o-mini-2024-07-18, input",
            ("gpt-4o-mini-2024-07-18", "input", "tokens", None),
        ),
        ("batch | gpt-4o, cached input", ("gpt-4o", "cached_input", "tokens", "batch")),
        ("web search", (None, None, "web_search", None)),
        ("gpt-4o, Cached Input", ("gpt-4o", "cached_input", "tokens", None)),
        ("Batch | gpt-4o, output", ("gpt-4o", "output", "tokens", "batch")),
        ("gpt-4o-2024-08-06, training", ("gpt-4o-2024-08-06", None, "other", None)),
        ("", (None, None, "other", None)),
    ],
)
def test_openai_line_item_grammar(
    line_item: str, expected: tuple[str | None, str | None, str, str | None]
) -> None:
    assert parse_openai_line_item(line_item) == expected


def test_openai_mapped_line_fields() -> None:
    line = map_openai_cost_result(
        DAY, openai_result(line_item="batch | gpt-4o-2024-08-06, input"), None, PATTERNS
    )

    assert line is not None
    assert line.provider == "openai"
    assert line.line_kind == "tokens"
    assert line.token_type == "input"
    assert line.model_raw == "gpt-4o-2024-08-06"
    assert line.model_pattern == "gpt-4o"
    assert line.service_tier == "batch"
    assert line.context_window is None
    assert line.provider_scope == "proj_abc123"
    assert line.description == "batch | gpt-4o-2024-08-06, input"
    assert line.quantity is None


def test_openai_negative_is_credit() -> None:
    line = map_openai_cost_result(
        DAY, openai_result(amount={"value": -0.5, "currency": "usd"}), None, []
    )

    assert line is not None
    assert line.line_kind == "credit"
    assert line.amount_usd == Decimal("-0.5")
    assert line.model_raw == "gpt-4o-2024-08-06"


@pytest.mark.parametrize(
    ("project_id", "scope", "kept"),
    [
        ("proj_abc123", None, True),
        ("proj_abc123", "proj_abc123", True),
        ("proj_abc123", "proj_other999", False),
        (None, "default", True),
        (None, "proj_abc123", False),
    ],
)
def test_openai_project_filter(project_id: str | None, scope: str | None, kept: bool) -> None:
    line = map_openai_cost_result(DAY, openai_result(project_id=project_id), scope, [])

    assert (line is not None) is kept


@pytest.mark.parametrize("currency", ["usd", "USD", "Usd"])
def test_openai_currency_case_insensitive(currency: str) -> None:
    line = map_openai_cost_result(
        DAY, openai_result(amount={"value": 0.1, "currency": currency}), None, []
    )

    assert line is not None
    assert line.amount_usd == Decimal("0.1")


@pytest.mark.parametrize("currency", ["eur", "EUR", "jpy"])
def test_openai_non_usd_raises(currency: str) -> None:
    with pytest.raises(UnsupportedCurrencyError):
        map_openai_cost_result(
            DAY, openai_result(amount={"value": 0.1, "currency": currency}), None, []
        )


def test_openai_fixture_page_maps_every_result() -> None:
    page = load_page("openai_costs_page.json")
    assert page["has_more"] is True
    lines: list[MappedLine] = []
    for bucket in page["data"]:
        day = datetime.fromtimestamp(bucket["start_time"], tz=UTC).date()
        for result in bucket["results"]:
            line = map_openai_cost_result(day, result, "proj_abc123", PATTERNS)
            if line is not None:
                lines.append(line)

    # The `proj_other999` line is skipped.
    assert len(lines) == 11
    assert {line.day for line in lines} == {date(2026, 10, 1), date(2026, 10, 2)}
    kinds = [line.line_kind for line in lines]
    assert kinds.count("tokens") == 8
    assert kinds.count("other") == 1
    assert kinds.count("web_search") == 1
    assert kinds.count("credit") == 1
    assert sum(line.amount_usd for line in lines if line.line_kind == "tokens") == Decimal("1.3555")


# --- line_key and patterns -----------------------------------------------------------------------


def test_line_key_is_stable_and_sensitive() -> None:
    base = (
        "proj_abc123",
        "gpt-4o-2024-08-06, input",
        "input",
        None,
        None,
        "gpt-4o-2024-08-06",
        "tokens",
    )

    key = line_key(*base)
    assert key == line_key(*base)
    assert len(key) == 64
    assert int(key, 16) >= 0

    changed_one_field = line_key(
        "proj_abc123",
        "gpt-4o-2024-08-06, input",
        "input",
        "batch",
        None,
        "gpt-4o-2024-08-06",
        "tokens",
    )
    assert changed_one_field != key

    # Nulls are kept in the canonical JSON, so an empty string is not the same as a missing value.
    assert line_key(None, "x", None, None, None, None, "other") != line_key(
        "", "x", None, None, None, None, "other"
    )
    assert line_key(None, "x", None, None, None, None, "other") != line_key(
        None, "x", None, None, None, "", "other"
    )


def test_line_key_matches_the_spec_formula() -> None:
    fields = [
        "wrkspc_01ABC",
        "Déjà vu",
        None,
        "standard",
        "0-200k",
        "claude-sonnet-4-5-20250929",
        "tokens",
    ]
    expected = hashlib.sha256(
        json.dumps(fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    key = line_key(
        "wrkspc_01ABC",
        "Déjà vu",
        None,
        "standard",
        "0-200k",
        "claude-sonnet-4-5-20250929",
        "tokens",
    )
    assert key == expected


def test_mapped_line_key_uses_its_own_fields() -> None:
    line = mapped(anthropic_result())

    assert line.line_key == line_key(
        line.provider_scope,
        line.description,
        line.token_type,
        line.service_tier,
        line.context_window,
        line.model_raw,
        line.line_kind,
    )


@pytest.mark.parametrize(
    ("model_raw", "expected"),
    [
        ("gpt-4o-2024-08-06", "gpt-4o"),
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
        ("gpt-4o-mini", "gpt-4o-mini"),
        ("gpt-4.1-nano", None),
        ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
        ("ft-gpt-4o-2024-08-06", None),
        (None, None),
    ],
)
def test_model_pattern_longest_match(model_raw: str | None, expected: str | None) -> None:
    assert model_pattern_for(model_raw, PATTERNS) == expected


def test_model_pattern_with_no_patterns_is_none() -> None:
    assert model_pattern_for("gpt-4o", []) is None


# --- CSV rows ------------------------------------------------------------------------------------


def test_csv_row_maps_to_a_line() -> None:
    line = map_csv_row(csv_row(), PATTERNS, today=TODAY)

    assert line == MappedLine(
        day=DAY,
        provider="anthropic",
        line_key=line.line_key,
        line_kind="tokens",
        model_raw="claude-sonnet-4-5-20250929",
        model_pattern="claude-sonnet-4-5",
        token_type="uncached_input",
        service_tier="standard",
        context_window=None,
        provider_scope=None,
        description="claude-sonnet-4-5-20250929 uncached_input",
        quantity=41150,
        amount_usd=Decimal("1.2345"),
    )


def test_csv_row_optional_columns_blank() -> None:
    line = map_csv_row(
        csv_row(
            line_kind="other",
            model="",
            token_type="",
            service_tier="",
            quantity="",
            description="Session Usage",
        ),
        PATTERNS,
        today=TODAY,
    )

    assert line.model_raw is None
    assert line.model_pattern is None
    assert line.token_type is None
    assert line.service_tier is None
    assert line.quantity is None
    assert line.description == "Session Usage"


def test_csv_row_default_description_without_model() -> None:
    assert (
        map_csv_row(csv_row(model="", token_type="output"), [], today=TODAY).description == "output"
    )
    assert (
        map_csv_row(
            csv_row(model="", token_type="", line_kind="other"), [], today=TODAY
        ).description
        == ""
    )


def test_csv_row_credit_may_be_negative_but_tokens_may_not() -> None:
    credit = map_csv_row(
        csv_row(line_kind="credit", amount_usd="-2.5", token_type=""), [], today=TODAY
    )
    assert credit.amount_usd == Decimal("-2.5")

    with pytest.raises(InvalidLineError) as excinfo:
        map_csv_row(csv_row(amount_usd="-2.5"), [], today=TODAY)
    assert excinfo.value.column == "amount_usd"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("day", "2026-13-01"),
        ("day", "20261001"),
        ("day", ""),
        ("provider", "azure"),
        ("provider", ""),
        ("line_kind", "fees"),
        ("amount_usd", "1.123456789"),
        ("amount_usd", "abc"),
        ("amount_usd", "NaN"),
        ("amount_usd", "Infinity"),
        ("amount_usd", ""),
        ("model", "m" * 257),
        ("token_type", "reasoning"),
        ("service_tier", "fast"),
        ("quantity", "-1"),
        ("quantity", "1.5"),
        ("quantity", "\u00b2"),
        ("quantity", "\u0663"),
        pytest.param("quantity", "9" * 5000, id="quantity-5000-digits"),
        ("quantity", "9" * 20),
        ("quantity", str(2**63)),
        ("amount_usd", "1E+9"),
        ("amount_usd", "1234567.5"),
        ("amount_usd", "1000000"),
        ("amount_usd", "1E+1000000"),
        ("amount_usd", "-1E+999999999"),
        ("description", "d" * 513),
        ("provider_scope", "s" * 129),
    ],
)
def test_csv_row_rejects_bad_cells(column: str, value: str) -> None:
    with pytest.raises(InvalidLineError) as excinfo:
        map_csv_row(csv_row(**{column: value}), [], today=TODAY)

    assert excinfo.value.column == column


def test_csv_row_trims_cells() -> None:
    line = map_csv_row(
        csv_row(provider=" openai ", model=" gpt-4o ", amount_usd=" 1.5 "), PATTERNS, today=TODAY
    )

    assert line.provider == "openai"
    assert line.model_raw == "gpt-4o"
    assert line.amount_usd == Decimal("1.5")


def test_csv_row_amount_out_of_range_names_the_column() -> None:
    with pytest.raises(InvalidLineError, match="amount_usd is out of range"):
        map_csv_row(csv_row(amount_usd="1234567.5"), [], today=TODAY)
    with pytest.raises(InvalidLineError, match="amount_usd is out of range"):
        map_csv_row(
            csv_row(line_kind="credit", token_type="", amount_usd="-1000000"), [], today=TODAY
        )


def test_csv_row_bounds_are_inclusive_of_the_column_limits() -> None:
    line = map_csv_row(
        csv_row(amount_usd="999999.99999999", quantity=str(2**63 - 1)), PATTERNS, today=TODAY
    )

    assert line.amount_usd == Decimal("999999.99999999")
    assert line.quantity == 2**63 - 1


@pytest.mark.parametrize(
    "cell",
    [
        # `Decimal()` reads both, but neither is a plain decimal as §8.4 spells it.
        "1_000",
        "\u0663",
        "1e3",
        ".5",
        "5.",
        "0x10",
    ],
)
def test_csv_row_amount_must_be_plain_ascii_decimal(cell: str) -> None:
    with pytest.raises(InvalidLineError) as excinfo:
        map_csv_row(csv_row(amount_usd=cell), [], today=TODAY)

    assert excinfo.value.column == "amount_usd"


@pytest.mark.parametrize(("cell", "usd"), [("+1.5", Decimal("1.5")), ("0", Decimal("0"))])
def test_csv_row_amount_plain_forms_accepted(cell: str, usd: Decimal) -> None:
    assert map_csv_row(csv_row(amount_usd=cell), [], today=TODAY).amount_usd == usd


@pytest.mark.parametrize(
    "cell",
    [
        "0001-01-01",
        "9999-12-31",
        "2025-09-10",  # 401 days before the upload's date
        "2026-10-17",  # the day after it
    ],
)
def test_csv_row_day_outside_the_retention_window_is_refused(cell: str) -> None:
    """A day the report could never read (older than the provider-line retention, or in the
    future) is refused: it would queue a backfill per month of the span it opens and overflow
    the date arithmetic at its edges."""
    with pytest.raises(InvalidLineError) as excinfo:
        map_csv_row(csv_row(day=cell), [], today=TODAY)

    assert excinfo.value.column == "day"
    assert "day" in str(excinfo.value) and cell not in str(excinfo.value)


@pytest.mark.parametrize("cell", ["2025-09-11", "2026-10-16"])
def test_csv_row_day_window_is_inclusive(cell: str) -> None:
    assert map_csv_row(csv_row(day=cell), [], today=TODAY).day == date.fromisoformat(cell)
