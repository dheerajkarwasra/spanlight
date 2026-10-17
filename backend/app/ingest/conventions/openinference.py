"""The OpenInference mapper (spec §4.3, `openinference` table).

Flattened `llm.input_messages.*` / `llm.output_messages.*` keys win over `input.value` /
`output.value`, which are parsed only under a JSON mime type. `llm.token_count.total` is derived
and ignored; the reasoning and audio details stay in the attributes. The `metadata` attribute is
parsed as a JSON object and handed over with its values as parsed (Bet 3's validator coerces and
drops them); anything that is not an object is left alone.
"""

from collections.abc import Mapping
from typing import Any

from app.ingest.conventions.flatten import Unflattened, unflatten
from app.ingest.conventions.kinds import openinference_kind
from app.ingest.conventions.read import as_text, first
from app.ingest.conventions.types import MappedFields, MappedUsage
from app.ingest.otlp_values import parse_content

_JSON = "application/json"


def map_openinference(attributes: Mapping[str, Any]) -> MappedFields:
    # Content keys are consumed whether or not they were read: a losing `input.value` must not
    # keep the request text in the stored attributes, past the payload capture rules and cap.
    consumed = {key for key in ("input.value", "output.value") if key in attributes}
    input_messages = unflatten(attributes, "llm.input_messages")
    output_messages = unflatten(attributes, "llm.output_messages")
    span_input = _content(
        input_messages,
        first(attributes, "input.value"),
        first(attributes, "input.mime_type"),
        consumed,
    )
    span_output = _content(
        output_messages,
        first(attributes, "output.value"),
        first(attributes, "output.mime_type"),
        consumed,
    )
    metadata = _metadata(first(attributes, "metadata"))
    if metadata is not None:
        consumed.add("metadata")
    span_kind = first(attributes, "openinference.span.kind")
    return MappedFields(
        provider=as_text(first(attributes, "llm.system", "llm.provider")),
        model=as_text(
            first(
                attributes,
                "llm.model_name",
                "llm.response.model_name",
                "llm.request.model_name",
                "embedding.model_name",
            )
        ),
        usage=MappedUsage(
            input_tokens=first(attributes, "llm.token_count.prompt"),
            output_tokens=first(attributes, "llm.token_count.completion"),
            cached_tokens=first(attributes, "llm.token_count.prompt_details.cache_read"),
            cache_write_tokens=first(attributes, "llm.token_count.prompt_details.cache_write"),
        ),
        input=span_input,
        output=span_output,
        kind=openinference_kind(span_kind),
        operation=None if span_kind is None else str(span_kind).strip().lower(),
        finish_reason_raw=as_text(first(attributes, "llm.finish_reason")),
        status_message_fallback=as_text(first(attributes, "exception.message")),
        metadata=metadata or {},
        consumed_keys=frozenset(consumed),
        dropped_messages=input_messages.dropped + output_messages.dropped,
    )


def _content(messages: Unflattened, value: Any, mime: Any, consumed: set[str]) -> Any:
    """The flattened messages when any were sent, else `value` (parsed under a JSON mime type).

    A prefix key that was consumed but dropped still counts as sent: the messages are then empty
    and `value` is ignored.
    """
    if messages.consumed:
        consumed.update(messages.consumed)
        return list(messages.items)
    if value is None:
        return None
    return parse_content(value) if mime == _JSON else value


def _metadata(value: Any) -> dict[str, object] | None:
    """The `metadata` attribute's pairs, keys lower-cased; `None` when it is not a JSON object."""
    parsed = parse_content(value)
    if not isinstance(parsed, dict):
        return None
    return {str(key).lower(): item for key, item in parsed.items()}
