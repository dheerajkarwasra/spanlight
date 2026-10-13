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
  span's cost is NULL, never 0.

Everything else (an over-long user or session id, a negative or huge count, any other count sent
as a string) still goes to validation and is reported per span, as for native spans.
"""

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

    @property
    def total(self) -> int:
        return self.environment + self.release + self.token_counts


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
        for field, value in usage.items():
            if isinstance(value, float) and value.is_integer():
                usage[field] = int(value)
            elif isinstance(value, float) or (
                isinstance(value, str) and value in _NON_FINITE_DOUBLES
            ):
                usage[field] = None
                dropped.token_counts += 1
