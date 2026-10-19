"""Vercel AI SDK legacy attributes (`ai.*`) onto `MappedFields` (spec §4.3, `vercel_ai` table).

`ai` 5 and 6 write these attributes natively; 7 writes them through `LegacyOpenTelemetry`.
The inner provider span also carries `gen_ai.*`, which only fills a field the `ai.*` name did
not (streamText writes its final counts under `ai.usage.*`). `ai.usage.reasoningTokens` and
`ai.usage.totalTokens` are derived or unpriced and stay in the stored attributes.
"""

from collections.abc import Mapping
from typing import Any

from app.ingest.conventions.kinds import vercel_kind
from app.ingest.conventions.read import as_text, first
from app.ingest.conventions.types import MappedFields, MappedUsage
from app.ingest.otlp_values import parse_content

# The provider id names the API family too (`openai.chat`); pricing keys on the vendor.
_PROVIDER_SUFFIXES = (".chat", ".messages", ".responses", ".completion", ".embedding")
# Content keys are consumed when present, read or not, so prompt text is stored only under the
# payload rules (capture toggle, 1 MiB cap), never in `attributes` (ruling C-1).
_INPUT_KEYS = ("ai.prompt.messages", "ai.prompt", "ai.toolCall.args", "ai.value", "ai.values")
_OUTPUT_KEYS = (
    "ai.response.text",
    "ai.response.toolCalls",
    "ai.response.object",
    "ai.toolCall.result",
)


def map_vercel_ai(attributes: Mapping[str, Any]) -> MappedFields:
    operation = as_text(first(attributes, "ai.operationId"))
    metadata, metadata_keys = _metadata(attributes)
    content_keys = frozenset(key for key in _INPUT_KEYS + _OUTPUT_KEYS if key in attributes)
    return MappedFields(
        provider=_provider(attributes),
        model=as_text(
            first(
                attributes,
                "ai.response.model",
                "gen_ai.response.model",
                "ai.model.id",
                "gen_ai.request.model",
            )
        ),
        usage=_usage(attributes),
        input=parse_content(
            first(
                attributes,
                "ai.prompt.messages",
                "ai.prompt",
                "ai.toolCall.args",
                "ai.value",
                "ai.values",
            )
        ),
        output=_output(attributes),
        kind=vercel_kind(operation),
        operation=operation,
        finish_reason_raw=as_text(first(attributes, "ai.response.finishReason")),
        time_to_first_token_ms=first(attributes, "ai.response.msToFirstChunk"),
        metadata=metadata,
        consumed_keys=content_keys | metadata_keys,
        trace_name=as_text(first(attributes, "ai.telemetry.functionId")),
    )


def _provider(attributes: Mapping[str, Any]) -> str | None:
    provider = as_text(first(attributes, "ai.model.provider"))
    if provider is None:
        return as_text(first(attributes, "gen_ai.system"))
    for suffix in _PROVIDER_SUFFIXES:
        if provider.endswith(suffix):
            return provider.removesuffix(suffix)
    return provider


def _usage(attributes: Mapping[str, Any]) -> MappedUsage:
    return MappedUsage(
        input_tokens=first(
            attributes, "ai.usage.inputTokens", "ai.usage.promptTokens", "gen_ai.usage.input_tokens"
        ),
        output_tokens=first(
            attributes,
            "ai.usage.outputTokens",
            "ai.usage.completionTokens",
            "gen_ai.usage.output_tokens",
        ),
        cached_tokens=first(
            attributes, "ai.usage.inputTokenDetails.cacheReadTokens", "ai.usage.cachedInputTokens"
        ),
        cache_write_tokens=first(attributes, "ai.usage.inputTokenDetails.cacheWriteTokens"),
    )


def _output(attributes: Mapping[str, Any]) -> Any:
    """`{text, tool_calls, object}` with absent members omitted; a tool span's result; else None."""
    members = {
        "text": first(attributes, "ai.response.text"),
        "tool_calls": parse_content(first(attributes, "ai.response.toolCalls")),
        "object": parse_content(first(attributes, "ai.response.object")),
    }
    output = {name: value for name, value in members.items() if value is not None}
    return output or parse_content(first(attributes, "ai.toolCall.result"))


def _metadata(attributes: Mapping[str, Any]) -> tuple[dict[str, object], frozenset[str]]:
    """Pairs under both metadata prefixes, keys lower-cased, first prefix wins per key."""
    telemetry = {k: v for k, v in attributes.items() if k.startswith("ai.telemetry.metadata.")}
    context = {k: v for k, v in attributes.items() if k.startswith("ai.settings.context.")}
    metadata: dict[str, object] = {}
    for pairs, prefix in ((telemetry, "ai.telemetry.metadata."), (context, "ai.settings.context.")):
        for key, value in pairs.items():
            metadata.setdefault(key.removeprefix(prefix).lower(), value)
    return metadata, frozenset(telemetry) | frozenset(context)
