"""The reconciliation report as CSV: one row per day and model group. Pure: no I/O.

Every text cell (the day, the flags, the group, the model pattern, the explained kinds) goes
through `safe_csv_cell`, which neutralizes a cell a spreadsheet would run as a formula: a group is
named after a model or an override pattern, both the organization's own text, and may start with
`=` or `-`. An amount or a ratio that is a strict decimal (`-0.005`) is written plain, so a
spreadsheet reads a negative figure as a number; one that is not falls back to `safe_csv_cell`.
An unknown value is an empty cell, never 0. Booleans are `true` and `false`; `explained_kinds` lists
the group's explained lines, `;`-separated, in the report's order.
"""

import csv
import io
import re
from collections.abc import Iterator, Sequence
from datetime import timedelta
from decimal import Decimal

from app.api.schemas.reconciliation import ReconciliationOut, ReportDayOut, ReportGroupOut
from app.exports.writers import safe_csv_cell

REPORT_CSV_COLUMNS: tuple[str, ...] = (
    "day",
    "provisional",
    "incomplete",
    "group",
    "model_pattern",
    "provider_usd",
    "spanlight_usd",
    "explained_usd",
    "residual_usd",
    "variance_ratio",
    "explained_kinds",
)
KIND_SEPARATOR = ";"
# The cells written plain: digits with an optional sign and fraction, nothing else.
_STRICT_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def report_csv_rows(report: ReconciliationOut) -> Iterator[bytes]:
    """The header, then one CRLF-terminated record per group of each day, in the report's order."""
    yield _record([safe_csv_cell(column) for column in REPORT_CSV_COLUMNS])
    for day in report.days:
        for group in day.groups:
            yield _record(_cells(day, group))


def report_filename(report: ReconciliationOut) -> str:
    """`reconciliation-<source id>-<first day>-<last day>.csv`, both days included."""
    first = report.window.start.date()
    last = (report.window.end - timedelta(days=1)).date()
    return f"reconciliation-{report.source.id}-{first.isoformat()}-{last.isoformat()}.csv"


def number_cell(text: str) -> str:
    """A number cell: plain when it is a strict decimal, else through `safe_csv_cell`."""
    return text if _STRICT_DECIMAL.fullmatch(text) else safe_csv_cell(text)


def _cells(day: ReportDayOut, group: ReportGroupOut) -> list[str]:
    text = (
        day.day.isoformat(),
        _flag(day.provisional),
        _flag(day.incomplete),
        group.group,
        group.model_pattern or "",
    )
    numbers = (
        group.provider_usd,
        group.spanlight_usd,
        group.explained_usd,
        group.residual_usd,
        group.variance_ratio,
    )
    kinds = KIND_SEPARATOR.join(line.kind for line in group.explained)
    return [
        *(safe_csv_cell(cell) for cell in text),
        *(number_cell(_decimal(value)) for value in numbers),
        safe_csv_cell(kinds),
    ]


def _record(cells: Sequence[str]) -> bytes:
    """One CSV record (CRLF-terminated, UTF-8) of cells already made safe; `csv` quotes cells
    holding commas, quotes or line breaks."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow(cells)
    return buffer.getvalue().encode()


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _decimal(value: Decimal | None) -> str:
    # `format(..., "f")` avoids scientific notation such as "0E-8".
    return "" if value is None else format(value, "f")
