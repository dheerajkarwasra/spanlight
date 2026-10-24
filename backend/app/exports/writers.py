"""Encoders for export files: pure functions from rows to bytes, with no I/O.

Each encoder yields one chunk per row (plus a header for CSV), so a caller can feed it rows as
they are read and never hold a whole export in memory. `csv_line` and `jsonl_line` encode a
single record for callers that read in batches and cannot hand over a plain iterable.

CSV cells are neutralized against formula injection (see `safe_csv_cell`): a spreadsheet opens
an export, and a trace name is attacker-controlled text.
"""

import csv
import io
import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.exports.schemas import SpanExportRow, TraceExportRow

TRACE_CSV_COLUMNS: tuple[str, ...] = (
    "trace_id",
    "name",
    "started_at",
    "ended_at",
    "duration_ms",
    "status",
    "environment",
    "release",
    "user_id",
    "session_id",
    "span_count",
    "error_count",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "tags",
    "metadata",
)

TAG_SEPARATOR = ";"
# A cell that starts with one of these is read as a formula by Excel, LibreOffice and Sheets.
# Tab and carriage return are included because spreadsheets also skip them before the check.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_csv_cell(value: str) -> str:
    """Neutralize a cell a spreadsheet would run as a formula by prefixing it with `'`."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def csv_line(cells: Sequence[str]) -> bytes:
    """One CSV record (CRLF-terminated, UTF-8). Every cell goes through `safe_csv_cell`.

    The `csv` module quotes cells that contain commas, quotes or line breaks.
    """
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow([safe_csv_cell(cell) for cell in cells])
    return buffer.getvalue().encode()


def csv_header() -> bytes:
    return csv_line(TRACE_CSV_COLUMNS)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def money_cell(value: Decimal | None) -> str:
    """Money as a decimal string; an unknown amount is an empty cell."""
    # `format(..., "f")` avoids scientific notation such as "0E-8".
    return "" if value is None else format(value, "f")


def trace_csv_line(row: TraceExportRow) -> bytes:
    """One trace as a CSV record. An unknown value is an empty cell, never `0`."""
    return csv_line(
        (
            row.trace_id,
            row.name or "",
            _iso(row.started_at),
            _iso(row.ended_at),
            f"{row.duration_ms:.3f}",
            row.status,
            row.environment or "",
            row.release or "",
            row.user_id or "",
            row.session_id or "",
            str(row.span_count),
            str(row.error_count),
            "" if row.input_tokens is None else str(row.input_tokens),
            "" if row.output_tokens is None else str(row.output_tokens),
            money_cell(row.cost_usd),
            TAG_SEPARATOR.join(row.tags),
            json.dumps(row.metadata, sort_keys=True, ensure_ascii=False),
        )
    )


def write_csv(rows: Iterable[TraceExportRow]) -> Iterator[bytes]:
    """The header, then one record per trace."""
    yield csv_header()
    for row in rows:
        yield trace_csv_line(row)


def _span_json(span: SpanExportRow) -> dict[str, Any]:
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "kind": span.kind,
        "name": span.name,
        "status": span.status,
        "status_message": span.status_message,
        "started_at": _iso(span.started_at),
        "ended_at": _iso(span.ended_at),
        "duration_ms": span.duration_ms,
        "provider": span.provider,
        "model": span.model,
        "input_tokens": span.input_tokens,
        "output_tokens": span.output_tokens,
        "cached_tokens": span.cached_tokens,
        "cache_write_tokens": span.cache_write_tokens,
        "cache_write_1h_tokens": span.cache_write_1h_tokens,
        "cost_usd": None if span.cost_usd is None else money_cell(span.cost_usd),
        "usage_rollup": span.usage_rollup,
        "pricing_version": span.pricing_version,
        "time_to_first_token_ms": span.time_to_first_token_ms,
        "input": span.input,
        "output": span.output,
        "attributes": span.attributes,
        "truncated": span.truncated,
    }


def _trace_json(row: TraceExportRow) -> dict[str, Any]:
    return {
        "trace_id": row.trace_id,
        "name": row.name,
        "environment": row.environment,
        "release": row.release,
        "external_user_id": row.user_id,
        "session_id": row.session_id,
        "tags": list(row.tags),
        "metadata": dict(row.metadata),
        "status": row.status,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "duration_ms": row.duration_ms,
        "span_count": row.span_count,
        "error_count": row.error_count,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": None if row.cost_usd is None else money_cell(row.cost_usd),
        "has_unpriced": row.has_unpriced,
    }


def _dump(document: dict[str, Any]) -> str:
    # Compact and unescaped; a JSON string cannot contain a raw newline, so one line is one trace.
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


# A trace line is written in three parts so that a trace with more spans than fit in memory can
# be streamed: the head (every trace field, then the opening of the `spans` array), each span,
# and the tail. `jsonl_line` is the three joined, for a trace whose spans are all at hand.
JSONL_TAIL = b"]}\n"


def jsonl_head(row: TraceExportRow) -> bytes:
    """The start of a trace line, up to and including the `[` that opens its `spans` array."""
    return (_dump(_trace_json(row))[:-1] + ',"spans":[').encode()


def jsonl_span(span: SpanExportRow, *, first: bool) -> bytes:
    """One span of the array; every span but the first is preceded by a comma."""
    return (("" if first else ",") + _dump(_span_json(span))).encode()


def jsonl_line(row: TraceExportRow) -> bytes:
    """One trace with its spans nested, as a single JSON line.

    The field names follow the trace detail endpoint. Money is a decimal string and unknown
    values are `null`. The span payloads are the stored ones, already redacted and truncated at
    ingest.
    """
    parts = [jsonl_head(row)]
    parts.extend(jsonl_span(span, first=index == 0) for index, span in enumerate(row.spans))
    parts.append(JSONL_TAIL)
    return b"".join(parts)


def write_jsonl(rows: Iterable[TraceExportRow]) -> Iterator[bytes]:
    """One line per trace."""
    for row in rows:
        yield jsonl_line(row)
