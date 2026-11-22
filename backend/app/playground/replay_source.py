"""Can a stored span be replayed, and as what? Pure: no database, no I/O (spec §6.2).

`classify` applies seven rules in order; the first match wins. An SDK or gateway span stores
the provider-native request body, so it replays exactly; an OTLP span stores only its messages,
so it replays as `messages_only`; anything else is refused with a stable code. A payload that
holds a redaction or depth marker anywhere (any key, any string, any depth) is refused: sending
`[REDACTED]` to a provider is spend without a request.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.core.redact import DEPTH_MARKER, REDACTED
from app.db.models import ProviderKind, SpanKind
from app.gateway.adapters import supports_surface
from app.gateway.errors import Surface
from app.playground.replay_errors import REFUSALS

Fidelity = Literal["exact", "modified", "messages_only"]
CredentialFamily = Literal["openai", "anthropic", "gemini"]
"""Which credential kinds can carry the request: `openai` is any server speaking the OpenAI API."""

SURFACE_ATTRIBUTE = "spanlight.gateway.surface"
_REQUEST_SURFACES = ("chat_completions", "responses", "messages", "generate_content")


def accepts(surface: Surface, provider: ProviderKind) -> bool:
    """Whether the chosen credential can send the stored request on its original surface."""
    return supports_surface(provider, surface)


@dataclass(frozen=True)
class SpanFacts:
    """What `classify` reads of a span; the service builds it from the row."""

    kind: str
    provider: str | None
    model: str | None
    name: str
    input: Any
    truncated: bool
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class ReplaySource:
    fidelity: Fidelity
    surface: Surface
    family: CredentialFamily
    body: dict[str, Any]


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: str


def _refusal(code: str) -> Refusal:
    return Refusal(code, REFUSALS[code])


def contains_markers(value: Any) -> bool:
    """Whether `[REDACTED]` or `[MAX_DEPTH]` appears in any key or string value, at any depth."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if REDACTED in item or DEPTH_MARKER in item:
                return True
        elif isinstance(item, Mapping):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


def is_request_body(value: Any, surface: Surface) -> bool:
    """Rule 5: a model and the request field for the recorded surface."""
    if not isinstance(value, Mapping) or not isinstance(value.get("model"), str):
        return False
    if surface == "generate_content":
        return isinstance(value.get("contents"), list)
    if surface == "responses":
        return isinstance(value.get("input"), (str, list))
    return isinstance(value.get("messages"), list)


def _is_message_list(value: Any) -> bool:
    """Rule 6: a non-empty list of `{role, content}` objects (what OTLP ingest stores)."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(m, Mapping) and "role" in m and "content" in m for m in value)
    )


def surface_of(body: Mapping[str, Any], span: SpanFacts) -> Surface:
    """The gateway's recorded surface when present, else by provider and body shape."""
    recorded = span.attributes.get(SURFACE_ATTRIBUTE)
    if recorded in _REQUEST_SURFACES:
        return cast(Surface, recorded)
    if "contents" in body:
        return "generate_content"
    if span.provider == "anthropic":
        return "messages"
    if "input" in body and "messages" not in body:
        return "responses"
    return "chat_completions"


def _source(fidelity: Fidelity, surface: Surface, body: dict[str, Any]) -> ReplaySource:
    family: CredentialFamily = "openai"
    if surface == "generate_content":
        family = "gemini"
    elif surface == "messages":
        family = "anthropic"
    return ReplaySource(fidelity, surface, family, body)


def classify(span: SpanFacts) -> ReplaySource | Refusal:
    """The seven rules of spec §6.2, in order."""
    if span.kind != SpanKind.LLM:
        return _refusal("REPLAY_NOT_LLM")
    if span.input is None:
        return _refusal("REPLAY_NO_PAYLOAD")
    # A truncated payload is stored as a string; a dict or list input is whole (the output was
    # the truncated side).
    if span.truncated and isinstance(span.input, str):
        return _refusal("REPLAY_PAYLOAD_TRUNCATED")
    if contains_markers(span.input):
        return _refusal("REPLAY_PAYLOAD_REDACTED")
    if isinstance(span.input, Mapping):
        body = dict(span.input)
        surface = surface_of(body, span)
        if surface == "generate_content" and "model" not in body:
            body["model"] = span.model
        if is_request_body(body, surface):
            return _source("exact", surface, body)
    if _is_message_list(span.input):
        surface = surface_of({}, span)
        if surface != "generate_content":
            key = "input" if surface == "responses" else "messages"
            return _source("messages_only", surface, {"model": span.model, key: span.input})
    return _refusal("REPLAY_UNSUPPORTED_INPUT")


EDITABLE_REFUSALS: frozenset[str] = frozenset(
    {"REPLAY_PAYLOAD_REDACTED", "REPLAY_PAYLOAD_TRUNCATED"}
)
"""The refusals an edit lifts: the payload is unsendable, but the user may have fixed it."""


def classify_edit(span: SpanFacts, edited_body: Mapping[str, Any]) -> ReplaySource | Refusal:
    """`classify`, except a redacted or truncated payload replays as the user's edit (spec §6.3).

    The edit is `modified` and keeps the span's family and surface; `build_replay` re-checks
    it for size, markers and shape. Every other refusal stands.
    """
    source = classify(span)
    if isinstance(source, Refusal) and source.code in EDITABLE_REFUSALS:
        body = dict(edited_body)
        original = span.input if isinstance(span.input, Mapping) else body
        surface = surface_of(original, span)
        if surface == "generate_content" and "model" not in body:
            body["model"] = span.model
        return _source("modified", surface, body)
    return source
