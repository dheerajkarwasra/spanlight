"""Strict parsing of an uploaded provider cost CSV (spec §7.6, §8.4, §12). Pure: no database.

Failure mode: a malformed or hostile file is half accepted, a cell is echoed back into an error
(it can hold anything), a formula is evaluated or rewritten, a huge file costs unbounded work, or
two rows for the same line are both stored and the report counts the line twice.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.reconciliation.csv_import import (
    CSV_COLUMNS,
    MAX_CSV_ERRORS,
    MAX_CSV_ROWS,
    MAX_UPLOAD_BYTES,
    CsvErrors,
    ParsedCsv,
    parse_cost_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"
PATTERNS = ["claude-sonnet-4-5", "claude-haiku-4-5"]
# The upload's UTC date; every day of the fixtures lies in the 400 days before it.
TODAY = date(2026, 10, 16)
HEADER = ",".join(CSV_COLUMNS)


def template() -> bytes:
    return (FIXTURES / "cost-template.csv").read_bytes()


def row(**cells: str) -> str:
    """One data row in `CSV_COLUMNS` order; cells not given are blank."""
    values = {
        "day": "2026-10-01",
        "provider": "anthropic",
        "line_kind": "tokens",
        "amount_usd": "1.00",
        **cells,
    }
    return ",".join(values.get(column, "") for column in CSV_COLUMNS)


def csv_bytes(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join((header, *rows)) + "\n").encode("utf-8")


def parse(data: bytes, provider: str = "anthropic") -> ParsedCsv | CsvErrors:
    return parse_cost_csv(data, provider=provider, patterns=PATTERNS, today=TODAY)


def parsed(data: bytes) -> ParsedCsv:
    result = parse(data)
    assert isinstance(result, ParsedCsv), result
    return result


def errors(data: bytes, provider: str = "anthropic") -> CsvErrors:
    result = parse(data, provider)
    assert isinstance(result, CsvErrors), result
    return result


def test_limits_are_the_documented_values() -> None:
    assert MAX_CSV_ROWS == 100_000
    assert MAX_CSV_ERRORS == 20
    assert MAX_UPLOAD_BYTES == 5 * 1024 * 1024
    assert CSV_COLUMNS == (
        "day",
        "provider",
        "line_kind",
        "amount_usd",
        "model",
        "token_type",
        "service_tier",
        "quantity",
        "description",
        "provider_scope",
    )


def test_template_parses() -> None:
    result = parsed(template())

    assert len(result.lines) == 3
    assert result.days == [date(2026, 10, 1), date(2026, 10, 2)]
    first, search, output = result.lines
    assert first.day == date(2026, 10, 1)
    assert first.provider == "anthropic"
    assert first.amount_usd == Decimal("1.2345")
    assert first.model_pattern == "claude-sonnet-4-5"
    assert first.token_type == "uncached_input"
    assert first.quantity == 411500
    assert first.description == "claude-sonnet-4-5 uncached_input"
    assert search.line_kind == "web_search"
    assert search.description == "Web search"
    assert output.day == date(2026, 10, 2)


def test_header_any_order() -> None:
    columns = list(reversed(CSV_COLUMNS))
    values = {
        "day": "2026-10-01",
        "provider": "anthropic",
        "line_kind": "tokens",
        "amount_usd": "2.5",
        "model": "claude-haiku-4-5",
        "token_type": "output",
    }
    data = csv_bytes(
        ",".join(values.get(column, "") for column in columns), header=",".join(columns)
    )

    [line] = parsed(data).lines
    assert line.amount_usd == Decimal("2.5")
    assert line.model_raw == "claude-haiku-4-5"
    assert line.token_type == "output"


def test_missing_column() -> None:
    header = ",".join(column for column in CSV_COLUMNS if column != "quantity")
    data = csv_bytes("2026-10-01,anthropic,tokens,1,,,,,", header=header)

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(1, "quantity")]
    assert "missing" in result.errors[0].message


def test_unknown_column() -> None:
    data = csv_bytes(row() + ",x", header=HEADER + ",cost_center")

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(1, None)]
    assert result.errors[0].message == "column 11 of the header is not a known column"


def test_duplicate_header_column() -> None:
    data = csv_bytes(row() + ",1", header=HEADER + ",day")

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(1, None)]
    assert result.errors[0].message == "column 11 of the header repeats an earlier column"


def test_bom_and_crlf() -> None:
    text = "\r\n".join((HEADER, row(), row(day="2026-10-02"))) + "\r\n"
    data = b"\xef\xbb\xbf" + text.encode("utf-8")

    result = parsed(data)
    assert result.days == [date(2026, 10, 1), date(2026, 10, 2)]
    assert all("\r" not in line.provider for line in result.lines)


def test_quoted_commas_in_description() -> None:
    data = csv_bytes(row(description='"Sonnet, batch, Oct ""1"""'))

    [line] = parsed(data).lines
    assert line.description == 'Sonnet, batch, Oct "1"'


def test_quoted_line_break_counts_as_one_row() -> None:
    data = csv_bytes(row(description='"two\nlines"'), row(day="bad"))

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(3, "day")]


def test_100001_rows_rejected() -> None:
    rows = [row(quantity=str(index)) for index in range(MAX_CSV_ROWS + 1)]
    data = csv_bytes(*rows)

    result = errors(data)
    assert len(result.errors) == 1
    assert result.errors[0].row == MAX_CSV_ROWS + 2
    assert result.errors[0].column is None
    assert "100000" in result.errors[0].message.replace(",", "")


def test_100000_rows_accepted() -> None:
    # Distinct descriptions keep every line distinct; short rows keep the file under 5 MB.
    data = csv_bytes(*(row(description=str(index)) for index in range(MAX_CSV_ROWS)))

    assert len(data) <= MAX_UPLOAD_BYTES
    assert len(parsed(data).lines) == MAX_CSV_ROWS


def test_oversize_bytes_rejected() -> None:
    data = csv_bytes(row()) + b" " * MAX_UPLOAD_BYTES

    result = errors(data)
    assert len(result.errors) == 1
    assert result.errors[0].column is None


def test_bad_day() -> None:
    data = csv_bytes(row(), row(day="01/10/2026"))

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(3, "day")]
    assert "01/10/2026" not in result.errors[0].message


def test_negative_non_credit() -> None:
    data = csv_bytes(row(amount_usd="-1.00"), row(line_kind="credit", amount_usd="-5"))

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(2, "amount_usd")]


def test_credit_may_be_negative() -> None:
    [line] = parsed(csv_bytes(row(line_kind="credit", amount_usd="-5"))).lines
    assert line.amount_usd == Decimal("-5")


def test_duplicate_key_names_both_rows() -> None:
    data = csv_bytes(
        row(model="claude-sonnet-4-5", token_type="output"),
        row(day="2026-10-02", model="claude-sonnet-4-5", token_type="output"),
        row(model="claude-sonnet-4-5", token_type="output", amount_usd="3"),
    )

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(4, None)]
    assert "row 2" in result.errors[0].message


def test_error_cap_is_20() -> None:
    data = csv_bytes(*(row(day="nope") for _ in range(30)))

    result = errors(data)
    assert len(result.errors) == MAX_CSV_ERRORS
    assert [e.row for e in result.errors] == list(range(2, 2 + MAX_CSV_ERRORS))


def test_nul_refused() -> None:
    data = csv_bytes(row(), row(description="a\x00b"), row(day="bad"))

    result = errors(data)
    assert len(result.errors) == 1
    assert result.errors[0].row == 3
    assert result.errors[0].column is None
    assert "\x00" not in result.errors[0].message


def test_formula_cell_kept_verbatim() -> None:
    formula = '=HYPERLINK("https://example.test/x","click")'
    quoted = '"' + formula.replace('"', '""') + '"'
    data = csv_bytes(row(description=quoted), row(day="2026-10-02", model="+cmd|' /C calc'!A0"))

    first, second = parsed(data).lines
    assert first.description == formula
    assert second.model_raw == "+cmd|' /C calc'!A0"


def test_provider_mismatch() -> None:
    data = csv_bytes(row(), row(provider="openai"))

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(3, "provider")]
    assert "anthropic" in result.errors[0].message


def test_wrong_cell_count() -> None:
    data = csv_bytes(row() + ",extra", row()[: row().rindex(",")])

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(2, None), (3, None)]


def test_blank_lines_skipped() -> None:
    data = csv_bytes(row(), "", row(day="2026-10-02"))

    assert len(parsed(data).lines) == 2


def test_no_data_rows() -> None:
    result = errors(csv_bytes())
    assert [(e.row, e.column) for e in result.errors] == [(1, None)]


def test_empty_file() -> None:
    result = errors(b"")
    assert [(e.row, e.column) for e in result.errors] == [(0, None)]


def test_data_row_as_header_never_echoed() -> None:
    secret = "sk-ant-api03-SECRETVALUE"
    header = row(description=secret, provider_scope="ws\x1b[31m\x07")
    data = csv_bytes(row(), header=header)

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(1, None)]
    assert result.errors[0].message == "the first row must be the header"
    for error in result.errors:
        assert secret not in (error.column or "") and secret not in error.message
        assert "\x1b" not in (error.column or "") and "\x1b" not in error.message


def test_unknown_header_name_never_echoed() -> None:
    secret = "sk-ant-api03-SECRETKEY"
    header = ",".join(("day", secret, *CSV_COLUMNS[1:]))
    data = csv_bytes("2026-10-01,x," + row()[len("2026-10-01,") :], header=header)

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(1, None)]
    assert result.errors[0].message == "column 2 of the header is not a known column"
    for error in result.errors:
        assert secret not in (error.column or "") and secret not in error.message


def test_unknown_header_errors_capped() -> None:
    extra = [f"extra_{index}" for index in range(30)]
    data = csv_bytes(row() + "," * 30, header=",".join((HEADER, *extra)))

    assert len(errors(data).errors) == MAX_CSV_ERRORS


def test_header_errors_use_its_real_row() -> None:
    header = ",".join(column for column in CSV_COLUMNS if column != "quantity")
    data = b"\n\n" + csv_bytes("2026-10-01,anthropic,tokens,1,,,,,", header=header)

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(3, "quantity")]


def test_nul_row_counts_cr_only_line_breaks() -> None:
    data = "\r".join((HEADER, row(), row(description="a\x00b"))).encode("utf-8")

    result = errors(data)
    assert [(e.row, e.column) for e in result.errors] == [(3, None)]


def test_not_utf8() -> None:
    data = csv_bytes(row(description="café")).replace("é".encode(), b"\xe9")

    result = errors(data)
    assert len(result.errors) == 1
    assert result.errors[0].column is None


def test_unterminated_quote_stops() -> None:
    data = csv_bytes(row(description='"open'), row(day="bad"))

    result = errors(data)
    assert len(result.errors) == 1
    assert result.errors[0].column is None


def test_error_message_never_echoes_cell() -> None:
    secret = "sk-ant-api03-SECRETVALUE"
    data = csv_bytes(
        row(token_type=secret),
        row(service_tier=secret),
        row(amount_usd=secret),
        row(quantity=secret),
        row(provider=secret),
        row(line_kind=secret),
    )

    result = errors(data)
    assert len(result.errors) == 6
    assert all(secret not in e.message for e in result.errors)


def test_days_outside_the_window_are_row_errors_naming_the_column() -> None:
    result = errors(csv_bytes(row(day="0001-01-01"), row(), row(day="9999-12-31")))

    assert [(error.row, error.column) for error in result.errors] == [(2, "day"), (4, "day")]
