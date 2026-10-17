"""OTLP convention registry: detect which exporter vocabulary a span speaks and map it (spec §4).

A convention's own marker key identifies its spans; `gen_ai.*` is the vocabulary Vercel, Traceloop
and LiteLLM all borrow, so it is the fallback rather than the signal: `map_fields` runs the
detected mapper first and lets `gen_ai` fill only what that mapper left empty.
"""

from collections.abc import Mapping
from dataclasses import fields, replace
from typing import Any

from app.ingest.conventions.gen_ai import map_gen_ai
from app.ingest.conventions.kinds import infer_kind, is_embeddings_operation
from app.ingest.conventions.openinference import map_openinference
from app.ingest.conventions.read import first, has_prefix
from app.ingest.conventions.traceloop import map_traceloop
from app.ingest.conventions.types import Convention, MappedFields, MappedUsage
from app.ingest.conventions.vercel_ai import map_vercel_ai

__all__ = ["Convention", "MappedFields", "MappedUsage", "detect", "map_fields"]

_MAPPERS = {
    Convention.GEN_AI: map_gen_ai,
    Convention.OPENINFERENCE: map_openinference,
    Convention.VERCEL_AI: map_vercel_ai,
    Convention.TRACELOOP: map_traceloop,
}
# Fields with their own merge rule in `fill_gaps`; every other field is taken from the fallback
# when the primary mapper left it `None`.
_MERGED = frozenset({"usage", "metadata", "consumed_keys", "dropped_messages", "kind"})


def detect(attributes: Mapping[str, Any]) -> Convention:
    """The first matching rule, in priority order; `NONE` when no convention key is present."""
    if first(attributes, "openinference.span.kind") is not None:
        return Convention.OPENINFERENCE
    if first(attributes, "ai.operationId") is not None or (
        has_prefix(attributes, "ai.") and first(attributes, "ai.model.id") is not None
    ):
        return Convention.VERCEL_AI
    if first(attributes, "traceloop.span.kind") is not None:
        return Convention.TRACELOOP
    if has_prefix(attributes, "gen_ai."):
        return Convention.GEN_AI
    return Convention.NONE


def map_fields(
    attributes: Mapping[str, Any], *, client_span: bool
) -> tuple[Convention, MappedFields]:
    """Detect the convention, run its mapper, fill gaps from `gen_ai.*`, apply §4.3 embeddings."""
    convention = detect(attributes)
    mapper = _MAPPERS.get(convention)
    if mapper is None:
        return convention, MappedFields(kind=infer_kind(attributes, client_span=client_span))
    mapped = mapper(attributes)
    if convention is not Convention.GEN_AI and has_prefix(attributes, "gen_ai."):
        mapped = fill_gaps(mapped, map_gen_ai(attributes))
    if is_embeddings_operation(convention, mapped.operation, attributes):
        mapped = _imply_embeddings_output(mapped)
    return convention, mapped


def fill_gaps(primary: MappedFields, fallback: MappedFields) -> MappedFields:
    """`primary` with every `None` field taken from `fallback`, usage field by field.

    Metadata keys of the primary win; consumed keys are unioned and dropped messages summed;
    the kind is always the primary mapper's.
    """
    changes: dict[str, Any] = {
        name: getattr(fallback, name)
        for name in (f.name for f in fields(MappedFields) if f.name not in _MERGED)
        if getattr(primary, name) is None
    }
    usage_changes = {
        f.name: getattr(fallback.usage, f.name)
        for f in fields(MappedUsage)
        if getattr(primary.usage, f.name) is None
    }
    return replace(
        primary,
        usage=replace(primary.usage, **usage_changes),
        metadata={**fallback.metadata, **primary.metadata},
        consumed_keys=primary.consumed_keys | fallback.consumed_keys,
        dropped_messages=primary.dropped_messages + fallback.dropped_messages,
        **changes,
    )


def _imply_embeddings_output(mapped: MappedFields) -> MappedFields:
    """Embeddings produce no output tokens: a reported input count implies `0`, flagged (§4.3)."""
    usage = mapped.usage
    if usage.input_tokens is None or usage.output_tokens is not None:
        return mapped
    return replace(mapped, usage=replace(usage, output_tokens=0), output_tokens_implied=True)
