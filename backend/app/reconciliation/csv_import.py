"""An uploaded provider cost CSV (§8.4) → `MappedLine`s, or the reasons it is refused (§7.6). Pure.

Strict on purpose: the file is UTF-8 (a BOM is stripped), its header holds exactly the §8.4
columns in any order, the `csv` module reads it with `strict=True`, and every row must name the
source's provider. Each data row goes through `map_csv_row`; a bad row is reported as
`{row, column, message}` and nothing is accepted while any error exists. At most
`MAX_CSV_ERRORS` are collected, then parsing stops.

Rows are numbered as a spreadsheet shows them: the header is row 1, the first data row is row 2,
and a quoted line break does not start a new row. Row 0 stands for the file as a whole.

Messages are fixed text naming a column or a row, never a cell's content: a cell can hold
anything. Cells are never evaluated; a formula-looking cell is stored as the text it is, and the
export escapes it. The bytes are read once and not kept.
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

from app.reconciliation.csv_rows import map_csv_row
from app.reconciliation.line_items import InvalidLineError
from app.reconciliation.types import CsvRow, MappedLine

__all__ = [
    "CSV_COLUMNS",
    "MAX_CSV_ERRORS",
    "MAX_CSV_ROWS",
    "MAX_UPLOAD_BYTES",
    "CsvError",
    "CsvErrors",
    "ParsedCsv",
    "parse_cost_csv",
]

# The §8.4 header, in the documented order (a file may use any order).
CSV_COLUMNS: tuple[str, ...] = (
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
MAX_CSV_ROWS = 100_000
MAX_CSV_ERRORS = 20
# Checked by the route before parsing; checked again here so the parser is safe on its own.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

FILE_ROW = 0
# A header name that is not a §8.4 column is file text, which can hold anything (a data row
# pasted above the header, a key): it is reported by position, never echoed.
_UNKNOWN = "column {position} of the header is not a known column"
_REPEATED = "column {position} of the header repeats an earlier column"


@dataclass(frozen=True)
class CsvError:
    """One reason the upload is refused: a spreadsheet row, the column when one is at fault."""

    row: int
    column: str | None
    message: str


@dataclass(frozen=True)
class ParsedCsv:
    """Every data row as a line, and the distinct days they cover, ascending."""

    lines: list[MappedLine]
    days: list[date]


@dataclass(frozen=True)
class CsvErrors:
    """The upload is refused; at most `MAX_CSV_ERRORS` reasons."""

    errors: list[CsvError]


@dataclass(frozen=True)
class _Record:
    row: int
    cells: list[str]


def parse_cost_csv(
    data: bytes, *, provider: str, patterns: Sequence[str], today: date
) -> ParsedCsv | CsvErrors:
    """The lines of an upload for a source of `provider`, or every reason (up to 20) to refuse it.

    `patterns` are the seed and override model patterns a line's model is matched against;
    `today` is the upload's UTC date, which bounds the days a row may name (`map_csv_row`).
    """
    text = _decode(data)
    if isinstance(text, CsvError):
        return CsvErrors([text])
    records = _read_records(text)
    if isinstance(records, CsvError):
        return CsvErrors([records])
    if not records:
        return CsvErrors([CsvError(FILE_ROW, None, "the file has no header row")])
    header_row = records[0].row
    header = [name.strip() for name in records[0].cells]
    header_errors = _header_errors(header, header_row)
    if header_errors:
        return CsvErrors(header_errors)
    if len(records) == 1:
        return CsvErrors([CsvError(header_row, None, "the file has no data rows")])
    return _map_rows(header, records[1:], provider, patterns, today)


def _decode(data: bytes) -> str | CsvError:
    """The text of the file, or why it cannot be read at all."""
    if len(data) > MAX_UPLOAD_BYTES:
        return CsvError(FILE_ROW, None, "the file is larger than 5 MB")
    nul = data.find(b"\x00")
    if nul >= 0:
        # The line of the first NUL (`\n`, `\r\n` or a lone `\r` ends a line); a UTF-16 file has
        # NULs everywhere, hence the hint. A quoted line break counts here, so it is approximate.
        line = data.count(b"\n", 0, nul) + data.count(b"\r", 0, nul) - data.count(b"\r\n", 0, nul)
        return CsvError(line + 1, None, "the file contains a NUL character; save it as UTF-8 text")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return CsvError(FILE_ROW, None, "the file must be UTF-8 text")


def _read_records(text: str) -> list[_Record] | CsvError:
    """The header and the non-blank data rows with their row numbers, or the one reason to stop.

    Reading stops at the first data row over `MAX_CSV_ROWS`, so a huge file is refused before
    any row is mapped, and at the first record the strict reader cannot parse: resuming inside a
    broken quote would read its tail as rows.
    """
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records: list[_Record] = []
    row = 0
    while True:
        row += 1
        try:
            cells = next(reader)
        except StopIteration:
            return records
        except csv.Error:
            return CsvError(row, None, "unreadable row: check its quotes; nothing was read")
        if not any(cell.strip() for cell in cells):
            continue
        if len(records) > MAX_CSV_ROWS:
            return CsvError(row, None, f"the file has more than {MAX_CSV_ROWS:,} data rows")
        records.append(_Record(row, cells))


def _header_errors(header: Sequence[str], row: int) -> list[CsvError]:
    """Each unknown, repeated or missing column of the header row, at most `MAX_CSV_ERRORS`.

    A first row holding none of the columns is most likely data with the header left out: it is
    one error, and none of its cells is reported.
    """
    if not set(header) & set(CSV_COLUMNS):
        return [CsvError(row, None, "the first row must be the header")]
    errors: list[CsvError] = []
    seen: set[str] = set()
    for position, name in enumerate(header, start=1):
        if name in seen:
            errors.append(CsvError(row, None, _REPEATED.format(position=position)))
        elif name not in CSV_COLUMNS:
            errors.append(CsvError(row, None, _UNKNOWN.format(position=position)))
        seen.add(name)
        if len(errors) >= MAX_CSV_ERRORS:
            return errors
    errors.extend(
        CsvError(row, column, "missing column") for column in CSV_COLUMNS if column not in seen
    )
    return errors[:MAX_CSV_ERRORS]


def _map_rows(
    header: list[str],
    records: Sequence[_Record],
    provider: str,
    patterns: Sequence[str],
    today: date,
) -> ParsedCsv | CsvErrors:
    """Every data row as a line; any error refuses the whole file."""
    lines: list[MappedLine] = []
    errors: list[CsvError] = []
    first_row: dict[tuple[date, str], int] = {}
    for record in records:
        result = _map_record(header, record, provider, patterns, today)
        if isinstance(result, MappedLine):
            key = (result.day, result.line_key)
            if key in first_row:
                result = CsvError(
                    record.row, None, f"the same day and line as row {first_row[key]}"
                )
            else:
                first_row[key] = record.row
                lines.append(result)
        if isinstance(result, CsvError):
            errors.append(result)
            if len(errors) >= MAX_CSV_ERRORS:
                break
    if errors:
        return CsvErrors(errors)
    return ParsedCsv(lines=lines, days=sorted({line.day for line in lines}))


def _map_record(
    header: list[str], record: _Record, provider: str, patterns: Sequence[str], today: date
) -> MappedLine | CsvError:
    """One data row as a line, or the first reason it is refused."""
    if len(record.cells) != len(header):
        return CsvError(
            record.row,
            None,
            f"the row has {len(record.cells)} cells; the header has {len(header)}",
        )
    # Every cell is text, and the header holds exactly the `CsvRow` keys.
    row = cast(CsvRow, dict(zip(header, record.cells, strict=True)))
    if row["provider"].strip() != provider:
        return CsvError(record.row, "provider", f"provider must be {provider}, the source's")
    try:
        return map_csv_row(row, patterns, today=today)
    except InvalidLineError as exc:
        # The mapper's messages are fixed text naming the column, never the cell.
        return CsvError(record.row, exc.column, str(exc))
