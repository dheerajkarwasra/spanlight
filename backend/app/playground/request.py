"""The provider request of one playground run. Pure: no database, no I/O.

A prompt version or typed messages become the provider's request. Gemini uses Generate Content,
xAI uses Responses, and Anthropic uses Messages. The other providers use Chat Completions.
"""

from typing import Any

from app.db.models import PromptVersion, ProviderKind
from app.gateway.errors import Surface
from app.playground.schemas import Params
from app.prompts.templates import Message, MissingVariablesError, Template, compile

# The error a configuration or experiment item stores when its rendered prompt passes the cap.
PROMPT_TOO_LARGE = "prompt_too_large"

# Anthropic requires `max_tokens`; this is used when the caller left it unset.
DEFAULT_ANTHROPIC_MAX_TOKENS = 1024


def surface_for(provider: ProviderKind) -> Surface:
    if provider == ProviderKind.GEMINI:
        return "generate_content"
    if provider == ProviderKind.XAI:
        return "responses"
    return "messages" if provider == ProviderKind.ANTHROPIC else "chat_completions"


def version_template(version: PromptVersion) -> Template:
    """The stored template of `version` (validated when it was written)."""
    stored = version.template
    if stored["kind"] == "text":
        return Template("text", text=stored["text"])
    return Template("chat", messages=[Message(m["role"], m["content"]) for m in stored["messages"]])


def compile_version(version: PromptVersion, variables: dict[str, str]) -> list[Message]:
    """The messages of `version` with `variables` filled in; a text prompt is one user message.

    Raises `MissingVariablesError` when a variable the template uses has no value, and
    `TemplateTooLargeError` when the rendered prompt would exceed 256 KB.
    """
    compiled = compile(version_template(version), variables)
    if compiled.messages is not None:
        return compiled.messages
    return [Message("user", compiled.text or "")]


def missing_variables_error(error: MissingVariablesError) -> str:
    return f"missing_variables: {', '.join(error.names)}"


def build_body(
    provider: ProviderKind,
    model: str,
    params: Params,
    messages: list[Message],
) -> dict[str, Any]:
    """The JSON body for `surface_for(provider)`."""
    surface = surface_for(provider)
    if surface == "generate_content":
        return _gemini_body(model, params, messages)
    body: dict[str, Any] = {"model": model}
    if surface == "responses":
        body["input"] = [_wire(m) for m in messages]
        body["store"] = False
        if params.max_tokens is not None:
            body["max_output_tokens"] = params.max_tokens
    elif surface == "messages":
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        body["messages"] = [_wire(m) for m in messages if m.role != "system"]
        if system:
            body["system"] = system
        body["max_tokens"] = params.max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS
    else:
        body["messages"] = [_wire(m) for m in messages]
        if params.max_tokens is not None:
            # OpenAI's newer models reject `max_tokens`; other servers only know that one.
            body[chat_cap_field(provider)] = params.max_tokens
    if params.temperature is not None:
        body["temperature"] = params.temperature
    if params.top_p is not None:
        body["top_p"] = params.top_p
    return body


def chat_cap_field(provider: ProviderKind) -> str:
    if provider in (ProviderKind.OPENAI, ProviderKind.AZURE_OPENAI, ProviderKind.GROQ):
        return "max_completion_tokens"
    return "max_tokens"


def _gemini_body(model: str, params: Params, messages: list[Message]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "contents": [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ],
    }
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    config = {
        key: value
        for key, value in (
            ("maxOutputTokens", params.max_tokens),
            ("temperature", params.temperature),
            ("topP", params.top_p),
        )
        if value is not None
    }
    if config:
        body["generationConfig"] = config
    return body


def _wire(message: Message) -> dict[str, str]:
    return {"role": message.role, "content": message.content}
