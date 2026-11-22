"""The `x-spanlight-*` request headers that join a gateway call to a trace the caller started.

`TraceHeaders.from_headers` reads them, dropping what is invalid. No database and no I/O: the
only side effects are the drop counter and the `metadata_header_dropped` log line, which name
the reason and never a value.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import structlog

from app.core.observability import METADATA_DROPPED
from app.core.text import clean_json
from app.ingest.jsondepth import exceeds_depth
from app.ingest.metadata import clean_metadata, record_metadata_drops

TRACE_ID_HEADER = "x-spanlight-trace-id"
PARENT_SPAN_ID_HEADER = "x-spanlight-parent-span-id"
SESSION_HEADER = "x-spanlight-session"
USER_HEADER = "x-spanlight-user"
TAGS_HEADER = "x-spanlight-tags"
RELEASE_HEADER = "x-spanlight-release"
METADATA_HEADER = "x-spanlight-metadata"
TRACE_NAME_HEADER = "x-spanlight-trace-name"
RUN_BUDGET_HEADER = "x-spanlight-run-budget-usd"
MAX_STEPS_HEADER = "x-spanlight-max-steps"
TRANSLATE_HEADER = "x-spanlight-translate"

MAX_RUN_BUDGET_USD = Decimal("100000")
MAX_STEPS_CEILING = 10_000

MAX_TAGS = 20
MAX_TAG_LENGTH = 64
MAX_TRACE_TEXT = 256  # user and session ids, as in native ingestion
MAX_RELEASE = 128  # as `TraceIn.release`
MAX_METADATA_HEADER_BYTES = 8192

_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")

HeaderDrop = Literal["too_large", "invalid_json", "too_deep", "not_object"]

logger = structlog.get_logger(__name__)


def _valid_id(value: str | None, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if not pattern.fullmatch(lowered) or not lowered.strip("0"):
        return None
    return lowered


def _bounded_text(value: str | None, limit: int = MAX_TRACE_TEXT) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if 0 < len(stripped) <= limit else None


def _tags(value: str | None) -> list[str]:
    if not value:
        return []
    kept: list[str] = []
    for raw in value.split(","):
        tag = raw.strip()
        if tag and len(tag) <= MAX_TAG_LENGTH and tag not in kept:
            kept.append(tag)
    return kept[:MAX_TAGS]


def _metadata(value: str | None) -> dict[str, str]:
    """The valid entries of the `x-spanlight-metadata` header (spec §4.7).

    A header that is not one JSON object of at most 8,192 bytes is dropped whole; an invalid
    entry is dropped on its own, and the first 32 valid keys in lexical order are kept. Every
    drop is counted under `path="gateway"`.
    """
    if value is None:
        return {}
    parsed = _header_object(value)
    if isinstance(parsed, str):
        METADATA_DROPPED.labels(path="gateway", reason="header").inc()
        logger.debug("metadata_header_dropped", reason=parsed)
        return {}
    cleaned, drops = clean_metadata(clean_json(parsed), strict=False)
    record_metadata_drops("gateway", drops)
    return cleaned


def _header_object(value: str) -> dict[str, Any] | HeaderDrop:
    """The header's JSON object, or why the whole header is dropped.

    The size is checked before parsing, so parsing costs at most 8 KiB per call. A `\\uXXXX`
    escape decodes here; a NUL or a lone surrogate is cleaned afterwards (`clean_json`).
    """
    # Header values arrive as latin-1 text (one character per byte); in-process callers may pass
    # any text, which UTF-8 bounds from above.
    if len(value.encode("utf-8", "surrogatepass")) > MAX_METADATA_HEADER_BYTES:
        return "too_large"
    try:
        parsed = json.loads(value)
    except RecursionError:  # nested deeper than the parser can follow
        return "too_deep"
    except ValueError:  # also an integer past the 4,300 digits Python converts
        return "invalid_json"
    if exceeds_depth(parsed):
        return "too_deep"
    if not isinstance(parsed, dict):
        return "not_object"
    return parsed


def parse_run_budget(value: str | None) -> Decimal | None:
    """Parse `x-spanlight-run-budget-usd` into a finite positive Decimal up to $100k."""
    if value is None:
        return None
    text = value.strip()
    if not text or len(text) > 32:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_RUN_BUDGET_USD:
        return None
    return amount.quantize(Decimal("0.00000001"))


def parse_max_steps(value: str | None) -> int | None:
    """Parse `x-spanlight-max-steps` into a positive integer up to 10,000."""
    if value is None:
        return None
    text = value.strip()
    if not text.isascii() or not text.isdigit() or len(text) > 6:
        return None
    steps = int(text)
    return steps if 1 <= steps <= MAX_STEPS_CEILING else None


@dataclass(frozen=True)
class TraceHeaders:
    """The trace a gateway call joins, read from the `x-spanlight-*` request headers."""

    trace_id: str | None = None
    parent_span_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] = field(default_factory=list)
    release: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    trace_name: str | None = None
    run_budget_usd: Decimal | None = None
    max_steps: int | None = None
    translate: bool = False

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "TraceHeaders":
        """Read the headers, dropping what is invalid.

        If either id is present but invalid, both are dropped and the call starts a new trace; a
        parent id without a trace id is dropped as well. User and session ids are 1 to 256
        characters and the release 1 to 128 (the bounds of native ingestion; a value outside
        them is dropped). Tags are comma-separated, at most 20 of at most 64 characters each
        (longer ones are dropped, repeats ignored). The trace name is 1 to 256 characters after
        trimming. The metadata is one JSON object (`_metadata`); nothing here rejects the call.
        """
        lowered = {name.lower(): value for name, value in headers.items()}
        raw_trace, raw_parent = lowered.get(TRACE_ID_HEADER), lowered.get(PARENT_SPAN_ID_HEADER)
        trace_id = _valid_id(raw_trace, _TRACE_ID)
        parent = _valid_id(raw_parent, _SPAN_ID)
        invalid = (raw_trace is not None and trace_id is None) or (
            raw_parent is not None and parent is None
        )
        if invalid or trace_id is None:
            trace_id, parent = None, None
        return cls(
            trace_id=trace_id,
            parent_span_id=parent,
            session_id=_bounded_text(lowered.get(SESSION_HEADER)),
            user_id=_bounded_text(lowered.get(USER_HEADER)),
            tags=_tags(lowered.get(TAGS_HEADER)),
            release=_bounded_text(lowered.get(RELEASE_HEADER), MAX_RELEASE),
            metadata=_metadata(lowered.get(METADATA_HEADER)),
            trace_name=_bounded_text(lowered.get(TRACE_NAME_HEADER)),
            run_budget_usd=parse_run_budget(lowered.get(RUN_BUDGET_HEADER)),
            max_steps=parse_max_steps(lowered.get(MAX_STEPS_HEADER)),
            translate=lowered.get(TRANSLATE_HEADER, "").strip().lower() == "true",
        )
