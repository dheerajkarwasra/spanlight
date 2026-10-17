"""`gen_ai.*` mapper: the OpenTelemetry GenAI conventions (spec §4.3 `gen_ai` table).

This is the body of the former `otlp._map_span`, with the rows the spec marks new: flattened
`gen_ai.prompt.N.*` / `gen_ai.completion.N.*` messages (Traceloop, LiteLLM legacy) read when the
JSON content keys are absent, `gen_ai.system_instructions` completing a list input, the operation
name deciding the kind, and time to first token. Usage goes through Bet 1's `map_usage`, which
owns the tier spellings and the web search count. `spanlight.metadata.*` is not read here:
`otlp_metadata.trace_metadata` is its one owner.
"""

from collections.abc import Mapping
from typing import Any

from app.ingest.conventions.flatten import unflatten
from app.ingest.conventions.kinds import operation_kind
from app.ingest.conventions.read import as_text, first, has_prefix
from app.ingest.conventions.types import MappedFields, MappedUsage
from app.ingest.otlp_usage import map_usage
from app.ingest.otlp_values import parse_content


def map_gen_ai(attributes: Mapping[str, Any]) -> MappedFields:
    provider = as_text(first(attributes, "gen_ai.provider.name", "gen_ai.system"))
    # Read before unflattening consumes the key; `raw_finish_reason` sees stored attributes only.
    finish_reason = as_text(first(attributes, "gen_ai.completion.0.finish_reason"))
    content, consumed, dropped = _content(attributes)
    operation = first(attributes, "gen_ai.operation.name")
    return MappedFields(
        provider=provider,
        # The response model is the concrete snapshot that was billed.
        model=as_text(first(attributes, "gen_ai.response.model", "gen_ai.request.model")),
        usage=_usage(attributes, provider),
        input=content.get("input"),
        output=content.get("output"),
        kind=operation_kind(as_text(operation)),
        operation=as_text(operation),
        finish_reason_raw=finish_reason,
        time_to_first_token_ms=_ttft_ms(
            first(
                attributes,
                "gen_ai.client.operation.time_to_first_chunk",
                "gen_ai.response.time_to_first_chunk",
            )
        ),
        consumed_keys=frozenset(consumed),
        dropped_messages=dropped,
    )


def _content(attributes: Mapping[str, Any]) -> tuple[dict[str, Any], set[str], int]:
    """Input and output, the keys consumed, and the flattened messages dropped past the limits.

    Every content key present is consumed, read or not (ruling C-1): a losing key left in the
    stored attributes would hold prompt text outside the payload rules.
    """
    consumed: set[str] = set()
    dropped = 0
    content: dict[str, Any] = {}
    for field_name, json_key, old_key in (
        ("input", "gen_ai.input.messages", "gen_ai.prompt"),
        ("output", "gen_ai.output.messages", "gen_ai.completion"),
    ):
        consumed.update(key for key in (json_key, old_key) if key in attributes)
        value = first(attributes, json_key, old_key)
        if value is not None:
            content[field_name] = parse_content(value)
        elif has_prefix(attributes, old_key + "."):
            flat = unflatten(attributes, old_key)
            content[field_name] = list(flat.items)
            consumed |= flat.consumed
            dropped += flat.dropped
    if "gen_ai.system_instructions" in attributes:
        consumed.add("gen_ai.system_instructions")
    system = first(attributes, "gen_ai.system_instructions")
    if system is not None and isinstance(content.get("input"), list):
        content["input"].insert(0, {"role": "system", "parts": parse_content(system)})
    return content, consumed, dropped


def _usage(attributes: Mapping[str, Any], provider: str | None) -> MappedUsage:
    usage, tier_raw = map_usage(attributes, provider)
    return MappedUsage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cached_tokens=usage.get("cached_tokens"),
        cache_write_tokens=usage.get("cache_write_tokens"),
        web_search_requests=usage.get("server_tool_use", {}).get("web_search_requests"),
        service_tier=usage.get("service_tier"),
        service_tier_raw=tier_raw,
    )


def _ttft_ms(seconds: Any) -> Any:
    """Seconds times 1000, numeric text included; other text is left for the fit to drop (§4.7)."""
    if isinstance(seconds, str):
        try:
            seconds = float(seconds)
        except ValueError:
            return seconds
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return seconds * 1000
    return seconds
