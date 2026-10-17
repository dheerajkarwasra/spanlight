"""Traceloop / OpenLLMetry mapper: workflow, task, agent and tool spans (spec §4.3).

Only spans carrying `traceloop.span.kind` come here. Traceloop's provider spans (`openai.chat`)
have no such key and speak `gen_ai.*`, so the `gen_ai` mapper reads them; provider, model and
usage therefore stay `None` here and the `gen_ai` fallback fills them in.
"""

from collections.abc import Mapping
from typing import Any

from app.ingest.conventions.kinds import traceloop_kind
from app.ingest.conventions.read import as_text, first
from app.ingest.conventions.types import MappedFields
from app.ingest.otlp_values import parse_content

_METADATA_PREFIX = "traceloop.association.properties."


def map_traceloop(attributes: Mapping[str, Any]) -> MappedFields:
    consumed: set[str] = set()
    content: dict[str, Any] = {}
    for field_name, key in (
        ("input", "traceloop.entity.input"),
        ("output", "traceloop.entity.output"),
    ):
        value = first(attributes, key)
        if value is not None:
            content[field_name] = parse_content(value)
            consumed.add(key)
    metadata: dict[str, object] = {}
    for key, value in attributes.items():
        if key.startswith("traceloop.association.properties."):
            metadata[key[len(_METADATA_PREFIX) :].lower()] = value
            consumed.add(key)
    span_kind = first(attributes, "traceloop.span.kind")
    return MappedFields(
        input=content.get("input"),
        output=content.get("output"),
        kind=traceloop_kind(span_kind),
        operation=as_text(span_kind),
        metadata=metadata,
        consumed_keys=frozenset(consumed),
        trace_name=as_text(first(attributes, "traceloop.workflow.name", "traceloop.entity.name")),
    )
