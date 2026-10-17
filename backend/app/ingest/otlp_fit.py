"""Fit mapped OTLP values into the native span fields, dropping a value rather than the span.

An OpenTelemetry exporter describes a whole service in its resource attributes, so one value
that does not fit a native column would otherwise reject every span the service sends. The
native schema (`SpanIn`, `TraceIn`) stays strict; only the OTLP mapping is lenient, and only
for these cases:

* `trace.environment` over 64 characters and `trace.release` over 128 are dropped (None).
* A token count sent as a double is an integer when it is whole (`12.0` becomes `12`) and is
  dropped when it has a fraction or is NaN or an infinity (which OTLP decoding has already
  turned into the strings "NaN", "Infinity" and "-Infinity"; a count sent as one of those
  strings cannot be told apart and is dropped too). A dropped count leaves usage unknown, so the
  span's cost is NULL, never 0. The web search count in `usage.server_tool_use` is fitted the
  same way and counted with the token counts; a dropped one leaves the fee unknown (NULL).
* A time to first token that is negative, not a finite number, or text that does not parse as
  one is dropped (`ttft`); numeric text such as "0.42" from a JSON exporter is read.

Invalid `spanlight.metadata.*` entries are dropped by `otlp_metadata` and counted here too
(`metadata`), so the OTLP response can name them in its `partialSuccess` warning. Messages a
convention mapper could not keep are counted as `messages`.

Everything else (an over-long user or session id, a negative or huge count, any other count sent
as a string) still goes to validation and is reported per span, as for native spans.
"""

import math
from dataclasses import dataclass
from typing import Any

# `TraceIn.environment` and `TraceIn.release` (`app.ingest.schemas`).
_TRACE_FIELD_LIMITS = (("environment", 64), ("release", 128))
_NON_FINITE_DOUBLES = frozenset({"NaN", "Infinity", "-Infinity"})


@dataclass
class DroppedValues:
    """How many values of each kind a batch dropped; never the values themselves."""

    environment: int = 0
    release: int = 0
    token_counts: int = 0
    metadata: int = 0
    ttft: int = 0
    messages: int = 0

    @property
    def total(self) -> int:
        return (
            self.environment
            + self.release
            + self.token_counts
            + self.metadata
            + self.ttft
            + self.messages
        )

    def metadata_warning(self) -> str | None:
        """The `partialSuccess` note for dropped metadata entries, by count only; None if none."""
        if not self.metadata:
            return None
        entries = "entry" if self.metadata == 1 else "entries"
        return (
            f"trace metadata: {self.metadata} {entries} dropped "
            "(invalid spanlight.metadata.* key or value, or over 32 keys)"
        )


def fit_span(native: dict[str, Any], dropped: DroppedValues) -> None:
    """Drop or coerce the values of one mapped span in place, counting each drop in `dropped`."""
    trace = native.get("trace")
    if isinstance(trace, dict):
        for field, limit in _TRACE_FIELD_LIMITS:
            value = trace.get(field)
            if isinstance(value, str) and len(value) > limit:
                trace[field] = None
                setattr(dropped, field, getattr(dropped, field) + 1)

    usage = native.get("usage")
    if isinstance(usage, dict):
        _fit_counts(usage, dropped)
        server_tool_use = usage.get("server_tool_use")
        if isinstance(server_tool_use, dict):
            _fit_counts(server_tool_use, dropped)

    ttft = native.get("time_to_first_token_ms")
    if ttft is not None:
        native["time_to_first_token_ms"] = fitted = _finite_non_negative(ttft)
        if fitted is None:
            dropped.ttft += 1
    dropped.messages += native.pop("dropped_messages", 0)


def _fit_counts(counts: dict[str, Any], dropped: DroppedValues) -> None:
    """Make each whole double count an integer and drop every other double, in place."""
    for field, value in counts.items():
        if isinstance(value, float) and value.is_integer():
            counts[field] = int(value)
        elif isinstance(value, float) or (isinstance(value, str) and value in _NON_FINITE_DOUBLES):
            counts[field] = None
            dropped.token_counts += 1


def _finite_non_negative(value: Any) -> int | float | None:
    """The value as a number if it is (or parses as) a finite one that is not negative."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return None
    return value
