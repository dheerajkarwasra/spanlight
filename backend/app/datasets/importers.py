"""Turning uploaded files and stored traces into dataset items. Pure: no database, no I/O.

A bad line never fails the file: it is reported with its line number and the rest is accepted.
Reasons name the problem, never echo the content of a line (it can hold prompts).
"""

import codecs
import csv
import json
from collections.abc import Iterator, Sequence
from typing import Any

from pydantic import ValidationError

from app.datasets.errors import UnsupportedFileError
from app.datasets.schemas import (
    MAX_ITEMS_PER_DATASET,
    MAX_PAYLOAD_BYTES,
    MAX_REPORTED_ERRORS,
    DatasetItemIn,
    LineError,
    ParsedItems,
    TraceItems,
    TraceRejection,
    TraceWithSpans,
)

CHUNK_BYTES = 64 * 1024
# The csv module counts characters and keeps the limit per process. Nothing else here reads CSV,
# so it is set once; the byte limit on a stored value is enforced by `DatasetItemIn`.
csv.field_size_limit(MAX_PAYLOAD_BYTES)
_RESERVED = ("input", "expected_output")
LLM_KIND = "llm"


def parse_upload(filename: str, data: bytes) -> ParsedItems:
    """Parse an uploaded file by its extension: `.jsonl` or `.csv`. Raises `UnsupportedFileError`
    for another extension or for bytes that are not UTF-8.

    Parsing stops once there are more items than a dataset may hold, so a huge file of tiny items
    costs no more than the limit; the service then refuses it as full.
    """
    name = filename.lower()
    if name.endswith(".jsonl"):
        return parse_jsonl(data)
    if name.endswith(".csv"):
        return parse_csv(data)
    raise UnsupportedFileError("upload a .jsonl or .csv file")


def parse_jsonl(data: bytes) -> ParsedItems:
    """One JSON object per line. `input` is required; `expected_output` is optional; every other
    key goes to `metadata` (an object under `metadata` is used as its base). Blank lines are
    skipped. Raises `UnsupportedFileError` when the bytes are not UTF-8.
    """
    text = _decode(data)
    parsed = ParsedItems()
    for number, line in enumerate(text.split("\n"), start=1):
        if _is_full(parsed):
            break
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            _reject(parsed, number, "not valid JSON")
            continue
        if not isinstance(record, dict):
            _reject(parsed, number, "not a JSON object")
            continue
        _accept(parsed, number, _jsonl_fields(record))
    return parsed


def parse_csv(data: bytes) -> ParsedItems:
    """A header row with an `input` column, then one item per record. `expected_output` is
    optional; every other column goes to `metadata` as a string. A cell of `input` or
    `expected_output` that holds a JSON object or array is stored as that value, anything else as
    text. Quoted commas and line breaks work. Raises `UnsupportedFileError` for non-UTF-8 bytes.
    """
    parsed = ParsedItems()
    reader = csv.reader(_lines(data))
    header = _read_header(reader)
    if header is None or "input" not in header:
        parsed.errors.append(LineError(1, "the header row needs an input column"))
        return parsed
    while not _is_full(parsed):
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error:
            # The reader would resume inside the broken field and accept its tail as records.
            parsed.errors.append(
                LineError(reader.line_num, "unreadable record; the rest is skipped")
            )
            break
        if not any(cell.strip() for cell in row):
            continue
        _accept(parsed, reader.line_num, _csv_fields(header, row))
    return parsed


def items_from_traces(traces: Sequence[TraceWithSpans], span_id: str | None) -> TraceItems:
    """One item per trace: the span named `span_id`, else the first `llm` span by start time.

    `input` and `expected_output` are the span's stored input and output (the recorded output is
    only a starting point for the expected answer). A trace without such a span, or whose span has
    no stored input, is rejected. Metadata is `{trace_id, span_id, model, environment}`.
    """
    result = TraceItems()
    for trace in traces:
        candidates = [
            span
            for span in trace.spans
            if (span.span_id == span_id if span_id is not None else span.kind == LLM_KIND)
        ]
        if not candidates:
            reason = "span not found" if span_id is not None else "no llm span"
            result.rejected.append(TraceRejection(trace.trace_id, reason))
            continue
        span = min(candidates, key=lambda candidate: (candidate.started_at, candidate.span_id))
        if span.input is None:
            result.rejected.append(TraceRejection(trace.trace_id, "no payload stored"))
            continue
        try:
            item = DatasetItemIn(
                input=span.input,
                expected_output=span.output,
                metadata={
                    "trace_id": trace.trace_id,
                    "span_id": span.span_id,
                    "model": span.model,
                    "environment": trace.environment,
                },
                source_trace_id=trace.trace_id,
                source_span_id=span.span_id,
            )
        except ValidationError as error:
            result.rejected.append(TraceRejection(trace.trace_id, _reason(error)))
            continue
        result.items.append(item)
    return result


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UnsupportedFileError("the file must be UTF-8 text") from None


def _lines(data: bytes) -> Iterator[str]:
    """The text of `data` line by line, split on `\\n` only and keeping the line end, so quoted
    line breaks survive and other separators stay inside their cell. The bytes are decoded in
    64 KB chunks; a multibyte character split across chunks is rejoined."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    pending = ""
    try:
        for start in range(0, len(data), CHUNK_BYTES):
            *complete, pending = (
                pending + decoder.decode(data[start : start + CHUNK_BYTES])
            ).split("\n")
            for line in complete:
                yield line + "\n"
        pending += decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        raise UnsupportedFileError("the file must be UTF-8 text") from None
    if pending:
        yield pending


def _read_header(reader: Any) -> list[str] | None:
    try:
        return [name.strip() for name in next(reader)]
    except (StopIteration, csv.Error):
        return None


def _jsonl_fields(record: dict[str, Any]) -> dict[str, Any]:
    base = record.get("metadata")
    metadata: dict[str, Any] = dict(base) if isinstance(base, dict) else {}
    for key, value in record.items():
        if key not in (*_RESERVED, "metadata") or (
            key == "metadata" and not isinstance(value, dict)
        ):
            metadata[key] = value
    return {
        "input": record.get("input"),
        "expected_output": record.get("expected_output"),
        "metadata": metadata,
    }


def _csv_fields(header: list[str], row: list[str]) -> dict[str, Any]:
    cells = {name: (row[index] if index < len(row) else "") for index, name in enumerate(header)}
    expected = cells.get("expected_output", "")
    return {
        "input": _cell_value(cells["input"]) if cells["input"].strip() else None,
        "expected_output": _cell_value(expected) if expected.strip() else None,
        "metadata": {
            name: value for name, value in cells.items() if name not in _RESERVED and name
        },
    }


def _cell_value(cell: str) -> Any:
    """The JSON object or array the cell holds, else the cell as text."""
    stripped = cell.strip()
    if stripped[:1] in ("{", "["):
        try:
            value = json.loads(stripped)
        except (ValueError, RecursionError):
            return cell
        if isinstance(value, dict | list):
            return value
    return cell


def _accept(parsed: ParsedItems, number: int, fields: dict[str, Any]) -> None:
    try:
        parsed.items.append(DatasetItemIn(**fields))
    except ValidationError as error:
        _reject(parsed, number, _reason(error))


def _reject(parsed: ParsedItems, number: int, reason: str) -> None:
    # A file of bad lines must not make a response of the same size.
    if len(parsed.errors) < MAX_REPORTED_ERRORS:
        parsed.errors.append(LineError(number, reason))


def _is_full(parsed: ParsedItems) -> bool:
    return len(parsed.items) > MAX_ITEMS_PER_DATASET


def _reason(error: ValidationError) -> str:
    """The first validation message, without the offending value."""
    first = error.errors(include_input=False, include_url=False)[0]
    where = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"]).removeprefix("Value error, ")
    return f"{where}: {message}" if where and where not in message else message
