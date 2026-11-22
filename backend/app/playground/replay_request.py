"""The request one replay sends. Pure: no database, no I/O (spec §6.3).

The body is the stored request (or the user's edit of it) with every key passed through
untouched except for streaming flags, candidate and output caps, standard billing options,
and a model override on the same surface.
Every change is recorded in `changed`; a replay is `exact` only when nothing but the streaming flags
moved, since those never change what the model is asked (`IGNORED_INPUT_KEYS`).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.budgets.gemini import supports_text_billing
from app.db.models import ProviderKind
from app.gateway.errors import Surface
from app.gateway.provider_billing import NEW_TEXT_PROVIDERS, supports_text_rates
from app.playground.replay_errors import REFUSALS, ReplayRefusedError
from app.playground.replay_source import (
    Fidelity,
    ReplaySource,
    accepts,
    contains_markers,
    is_request_body,
)
from app.playground.request import DEFAULT_ANTHROPIC_MAX_TOKENS, chat_cap_field
from app.playground.schemas import MAX_OUTPUT_TOKENS

MAX_EDITED_BODY_BYTES = 256 * 1024

# Every output cap a provider body may carry: chat completions (both spellings), Responses.
_OUTPUT_CAP_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens")
# The cap injected when a body has none, so the budget's worst case is what the provider enforces.
_DEFAULT_CAP_KEY: dict[str, str] = {
    "messages": "max_tokens",
    "responses": "max_output_tokens",
}
# Billed beyond tokens (a priority tier, per-call tool fees) and not in the replay estimate.
_STANDARD_TIERS = (None, "default", "standard_only")
# Tool types billed by tokens alone: functions, and tools the caller's own code executes. Any
# other type (a provider-run server tool, known or new) is refused, so the list fails safe.
_TOKEN_ONLY_TOOLS = ("function", "custom")
_CLIENT_TOOL_PREFIXES = (
    "bash_",
    "text_editor_",
    "computer",
    "memory_",
    "browser_",
    "local_shell",
    "apply_patch",
)
_STREAM_KEYS = ("stream", "stream_options")
_EXACT_CHANGES = frozenset(_STREAM_KEYS)


@dataclass(frozen=True)
class ReplayPlan:
    body: dict[str, Any]
    surface: Surface
    model: str
    fidelity: Fidelity
    changed: tuple[str, ...]
    max_tokens: int
    """The request's output cap. Some providers have separate limits for billed thinking."""


def _refuse(code: str, **fmt: str) -> ReplayRefusedError:
    return ReplayRefusedError(code, REFUSALS[code].format(**fmt))


def _edited(edited_body: Mapping[str, Any], source: ReplaySource) -> dict[str, Any]:
    """Rules 4, 5 and 7 again on the user's edit: markers cannot be sent, the shape stays a
    request body."""
    text = json.dumps(edited_body, ensure_ascii=False)
    if len(text.encode("utf-8", "surrogatepass")) > MAX_EDITED_BODY_BYTES:
        raise _refuse("REPLAY_BODY_TOO_LARGE")
    if contains_markers(edited_body):
        raise _refuse("REPLAY_PAYLOAD_REDACTED")
    body = dict(edited_body)
    if source.surface == "generate_content" and "model" not in body:
        body["model"] = source.body.get("model")
    if not is_request_body(body, source.surface):
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    return body


def _standard_tier(body: dict[str, Any], surface: Surface, changed: list[str]) -> None:
    """`auto` may route to a priority tier; send the surface's standard tier instead."""
    if body.get("service_tier") == "auto":
        body["service_tier"] = "standard_only" if surface == "messages" else "default"
        changed.append("service_tier")


def _unpriced_option(body: Mapping[str, Any]) -> str | None:
    """The first body option the replay budget cannot price: a non-standard `service_tier`, a
    chat `web_search_options`, or a tool that is not billed by tokens alone."""
    if body.get("service_tier") not in _STANDARD_TIERS:
        return "service_tier"
    if "web_search_options" in body:
        return "web_search_options"
    tools = body.get("tools")
    for tool in tools if isinstance(tools, list) else ():
        kind = tool.get("type") if isinstance(tool, dict) else None
        if kind is None or kind in _TOKEN_ONLY_TOOLS:
            continue
        if kind == "shell":
            # OpenAI's shell runs in a billed hosted container unless the caller runs it locally.
            environment = tool.get("environment")
            if isinstance(environment, dict) and environment.get("type") == "local":
                continue
            return "tools (shell)"
        if not isinstance(kind, str) or not kind.startswith(_CLIENT_TOOL_PREFIXES):
            return f"tools ({kind})"
    return None


def _clamp_caps(
    body: dict[str, Any],
    surface: Surface,
    provider: ProviderKind,
    override: int | None,
    changed: list[str],
) -> int:
    """Clamp every output cap in `body` to `min(stored, override, MAX_OUTPUT_TOKENS)`; give the
    `messages` surface its default and the OpenAI surfaces the ceiling when they have none.
    Returns the worst case that remains."""
    ceiling = min(override or MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS)
    if surface == "generate_content":
        return _clamp_gemini(body, ceiling, changed)
    if surface == "chat_completions":
        key = (
            chat_cap_field(provider) if provider in NEW_TEXT_PROVIDERS else "max_completion_tokens"
        )
    else:
        key = _DEFAULT_CAP_KEY[surface]
    present = [key for key in _OUTPUT_CAP_KEYS if key in body]
    if not present:
        worst = min(DEFAULT_ANTHROPIC_MAX_TOKENS, ceiling) if surface == "messages" else ceiling
        body[key] = worst
        changed.append(key)
        return worst
    if surface != "chat_completions" and present != [key]:
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    if surface == "chat_completions" and "max_output_tokens" in present:
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    for field in present:
        stored = body[field]
        if type(stored) is not int or stored <= 0:
            raise _refuse("REPLAY_UNSUPPORTED_INPUT")
        body[field] = min(stored, ceiling)
        if body[field] != stored:
            changed.append(field)
    if provider in NEW_TEXT_PROVIDERS and key not in body:
        # The alternate spelling is not enforced by all providers.
        body[key] = body.pop(present[0])
        if present[0] not in changed:
            changed.append(present[0])
        changed.append(key)
        return int(body[key])
    return max(int(body[field]) for field in present)


def _clamp_gemini(body: dict[str, Any], ceiling: int, changed: list[str]) -> int:
    config = dict(body.get("generationConfig", {}))
    cap = config.get("maxOutputTokens", ceiling)
    count = config.get("candidateCount", 1)
    if type(cap) is not int or cap <= 0 or type(count) is not int or count <= 0:
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    if count > 1:
        config["candidateCount"] = 1
        changed.append("generationConfig.candidateCount")
    if "maxOutputTokens" not in config or cap > ceiling:
        config["maxOutputTokens"] = min(cap, ceiling)
        changed.append("generationConfig.maxOutputTokens")
    body["generationConfig"] = config
    return min(cap, ceiling)


def _prepare_body(
    body: dict[str, Any], surface: Surface, provider: ProviderKind, changed: list[str]
) -> None:
    for key in _STREAM_KEYS:
        if key in body:
            del body[key]
            changed.append(key)
    if "n" in body:
        if type(body["n"]) is not int or body["n"] <= 0:
            raise _refuse("REPLAY_UNSUPPORTED_INPUT")
        if body["n"] > 1:
            body["n"] = 1
            changed.append("n")
    _standard_tier(body, surface, changed)
    option = _unpriced_option(body)
    if option is not None:
        raise _refuse("REPLAY_UNPRICED_OPTION", option=option)
    if provider == ProviderKind.GEMINI:
        if surface != "generate_content" or not supports_text_billing(body):
            raise _refuse("REPLAY_UNPRICED_OPTION", option="Gemini request format")
    elif provider in NEW_TEXT_PROVIDERS:
        if not supports_text_rates(body):
            raise _refuse("REPLAY_UNPRICED_OPTION", option="provider request format")
    # The legacy formats accept client-executed tools vetted by _unpriced_option.
    elif surface in ("chat_completions", "responses") and not supports_text_rates(
        {**body, "tools": None}
    ):
        raise _refuse("REPLAY_UNPRICED_OPTION", option="request format")


def build_replay(
    source: ReplaySource,
    *,
    credential_provider: ProviderKind,
    model_override: str | None,
    max_tokens_override: int | None,
    edited_body: Mapping[str, Any] | None,
) -> ReplayPlan:
    """Raises `ReplayRefusedError` for an incompatible credential, an invalid or oversized edit,
    an option the budget cannot price, or a source with no model."""
    if not accepts(source.surface, credential_provider):
        raise _refuse("REPLAY_PROVIDER_MISMATCH", provider=source.surface)
    changed: list[str] = []
    if edited_body is None:
        body = dict(source.body)
    else:
        body = _edited(edited_body, source)
        changed.append("body")
    _prepare_body(body, source.surface, credential_provider, changed)
    max_tokens = _clamp_caps(
        body, source.surface, credential_provider, max_tokens_override, changed
    )
    if model_override is not None and model_override != body.get("model"):
        body["model"] = model_override
        changed.append("model")
    model = body.get("model")
    if not isinstance(model, str):
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    fidelity: Fidelity = "modified"
    if source.fidelity == "messages_only":
        fidelity = "messages_only"
    elif source.fidelity == "exact" and set(changed) <= _EXACT_CHANGES:
        fidelity = "exact"
    return ReplayPlan(body, source.surface, model, fidelity, tuple(changed), max_tokens)
