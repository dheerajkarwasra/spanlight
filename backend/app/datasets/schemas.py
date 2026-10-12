"""Dataset values the importers, the service and the API share.

Bounds (spec): `input` and `expected_output` at most 64 KB of JSON each, `metadata` at most 8 KB.
A value that is too large is a validation error on the item, never silently truncated.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.text import NUL, without_nul

MAX_ITEMS_PER_DATASET = 10_000
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_METADATA_BYTES = 8 * 1024

DatasetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_REPORTED_ERRORS = 100


def _json_size(value: Any) -> int:
    """The bytes `value` takes as compact JSON; a value JSON cannot hold (NaN, Infinity, a lone
    surrogate) is `ValueError`."""
    try:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return len(text.encode())
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("must be valid JSON") from error


def _within(value: Any, limit: int, label: str) -> Any:
    """`value` without NUL, or `ValueError` when it is not JSON or is over `limit` bytes."""
    value = without_nul(value)
    if _json_size(value) > limit:
        raise ValueError(f"{label} is larger than {limit // 1024} KB")
    return value


class DatasetSpec(BaseModel):
    """The body that creates a dataset."""

    model_config = ConfigDict(extra="forbid")

    name: DatasetName
    description: Description | None = None


class DatasetPatch(BaseModel):
    """The body that edits a dataset; only the description changes. `null` clears it."""

    model_config = ConfigDict(extra="forbid")

    description: Description | None = None


class DatasetItemIn(BaseModel):
    """One item to store. `input` is required and may be any JSON except null."""

    model_config = ConfigDict(extra="forbid")

    input: Any = Field(...)
    expected_output: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_trace_id: str | None = Field(default=None, max_length=200)
    source_span_id: str | None = Field(default=None, max_length=200)

    @field_validator("input")
    @classmethod
    def _input_present_and_small(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("input is required")
        return _within(value, MAX_PAYLOAD_BYTES, "input")

    @field_validator("expected_output")
    @classmethod
    def _expected_small(cls, value: Any) -> Any:
        return None if value is None else _within(value, MAX_PAYLOAD_BYTES, "expected_output")

    @field_validator("metadata")
    @classmethod
    def _metadata_small(cls, value: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = _within(value, MAX_METADATA_BYTES, "metadata")
        return cleaned

    @field_validator("source_trace_id", "source_span_id")
    @classmethod
    def _source_without_nul(cls, value: str | None) -> str | None:
        return None if value is None else value.replace(NUL, "")


class ItemPatch(BaseModel):
    """The body that edits an item: the fields sent change, `null` clears `expected_output`."""

    model_config = ConfigDict(extra="forbid")

    expected_output: Any | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("expected_output")
    @classmethod
    def _expected_small(cls, value: Any) -> Any:
        return None if value is None else _within(value, MAX_PAYLOAD_BYTES, "expected_output")

    @field_validator("metadata")
    @classmethod
    def _metadata_small(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        cleaned: dict[str, Any] | None = (
            None if value is None else _within(value, MAX_METADATA_BYTES, "metadata")
        )
        return cleaned


@dataclass(frozen=True, slots=True)
class LineError:
    """A line (JSONL) or record (CSV) that was not accepted, and why. 1-based."""

    line: int
    reason: str


@dataclass(frozen=True, slots=True)
class ParsedItems:
    """What an importer found: the items it accepted and the lines it rejected."""

    items: list[DatasetItemIn] = field(default_factory=list)
    errors: list[LineError] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SpanSource:
    """The stored fields of a span that an item can be copied from."""

    span_id: str
    kind: str
    started_at: datetime
    model: str | None
    input: Any | None
    output: Any | None


@dataclass(frozen=True, slots=True)
class TraceWithSpans:
    """A trace of the project and the candidate spans loaded for it."""

    trace_id: str
    environment: str | None
    spans: Sequence[SpanSource]


@dataclass(frozen=True, slots=True)
class TraceRejection:
    """A trace that gave no item, and why."""

    trace_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TraceItems:
    """What `items_from_traces` found."""

    items: list[DatasetItemIn] = field(default_factory=list)
    rejected: list[TraceRejection] = field(default_factory=list)
