"""Request fields whose billing is covered by the new adapters' text rates."""

from collections.abc import Mapping
from typing import Any

from app.db.models import ProviderKind

NEW_TEXT_PROVIDERS = frozenset(
    {
        ProviderKind.AZURE_OPENAI,
        ProviderKind.MISTRAL,
        ProviderKind.GROQ,
        ProviderKind.DEEPSEEK,
        ProviderKind.XAI,
        ProviderKind.TOGETHER,
    }
)

_HOSTED_FIELDS = (
    "audio",
    "background",
    "context_management",
    "conversation",
    "data_sources",
    "defer",
    "deferred",
    "enable_search",
    "image_config",
    "previous_response_id",
    "prompt",
    "search_parameters",
    "web_search_options",
    "web_tools",
    "server_side_tools",
    "compound_custom",
    "safety_model",
)
_TEXT_PARTS = ("text", "input_text", "output_text", "refusal")
_TEXT_ITEMS = (None, "message", "reasoning", "function_call", "function_call_output")


def supports_text_rates(body: Mapping[str, Any]) -> bool:
    """Only one text model invocation, with any function tools run by the client."""
    # An empty options object can activate the provider's hosted-tool defaults.
    if any(body.get(key) is not None and body[key] is not False for key in _HOSTED_FIELDS):
        return False
    if body.get("modalities") not in (None, ["text"]):
        return False
    tools = body.get("tools")
    if tools is not None and (
        not isinstance(tools, list)
        or any(not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools)
    ):
        return False
    content = body.get("messages", body.get("input"))
    if isinstance(content, str):
        return True
    return isinstance(content, list) and all(_text_message(item) for item in content)


def _text_message(item: object) -> bool:
    if not isinstance(item, dict) or item.get("type") not in _TEXT_ITEMS:
        return False
    content = item.get("content")
    if content is None or isinstance(content, str):
        return True
    return isinstance(content, list) and all(
        isinstance(part, dict) and part.get("type") in _TEXT_PARTS for part in content
    )
