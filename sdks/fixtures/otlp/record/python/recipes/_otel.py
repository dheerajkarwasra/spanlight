"""Tracer provider shared by the Python recipes.

Span and trace ids come from a generator seeded with the scenario name, so a re-record of the
same scenario yields the same ids and `record.py --check` can diff `expected` field by field.
Export is OTLP http/protobuf to the capture server named by
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`; `flush` drains the batch before the recipe process exits.
"""

import random
import zlib

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import IdGenerator


class SeededIdGenerator(IdGenerator):
    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)  # noqa: S311 - reproducible ids, not security

    def generate_span_id(self) -> int:
        return self._rng.getrandbits(64)

    def generate_trace_id(self) -> int:
        return self._rng.getrandbits(128)


def configure(scenario: str) -> TracerProvider:
    """Install the global tracer provider for one scenario; call before instrumenting."""
    provider = TracerProvider(id_generator=SeededIdGenerator(zlib.crc32(scenario.encode())))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def flush(provider: TracerProvider) -> None:
    provider.force_flush()
    provider.shutdown()
