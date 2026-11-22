"""Provider adapters: one per `ProviderKind`, looked up with `adapter_for`."""

from app.db.models import ProviderKind
from app.gateway.adapters.anthropic import AnthropicAdapter
from app.gateway.adapters.azure import AzureOpenAiAdapter
from app.gateway.adapters.base import (
    UPSTREAM_REQUEST_ID_HEADER,
    CredentialEndpoint,
    InvalidUpstreamBody,
    ProviderAdapter,
    UnsupportedSurface,
    UpstreamRequest,
    UpstreamResponse,
)
from app.gateway.adapters.fixed_chat import FixedChatAdapter
from app.gateway.adapters.gemini import GeminiAdapter
from app.gateway.adapters.openai import OpenAiAdapter
from app.gateway.adapters.openai_compatible import OpenAiCompatibleAdapter
from app.gateway.errors import Surface

_ADAPTERS: dict[ProviderKind, ProviderAdapter] = {
    ProviderKind.OPENAI: OpenAiAdapter(),
    ProviderKind.ANTHROPIC: AnthropicAdapter(),
    ProviderKind.GEMINI: GeminiAdapter(),
    ProviderKind.OPENAI_COMPATIBLE: OpenAiCompatibleAdapter(),
    ProviderKind.AZURE_OPENAI: AzureOpenAiAdapter(),
    ProviderKind.MISTRAL: FixedChatAdapter(
        ProviderKind.MISTRAL, "https://api.mistral.ai/v1", chat_stream_usage=True
    ),
    ProviderKind.GROQ: FixedChatAdapter(
        ProviderKind.GROQ, "https://api.groq.com/openai/v1", chat_stream_usage=False
    ),
    ProviderKind.DEEPSEEK: FixedChatAdapter(
        ProviderKind.DEEPSEEK, "https://api.deepseek.com", chat_stream_usage=True
    ),
    ProviderKind.XAI: FixedChatAdapter(
        ProviderKind.XAI, "https://api.x.ai/v1", chat_stream_usage=True, responses=True
    ),
    ProviderKind.TOGETHER: FixedChatAdapter(
        ProviderKind.TOGETHER, "https://api.together.ai/v1", chat_stream_usage=False
    ),
}


def adapter_for(provider: ProviderKind) -> ProviderAdapter:
    return _ADAPTERS[provider]


def supports_surface(provider: ProviderKind, surface: Surface) -> bool:
    adapter = _ADAPTERS.get(provider)
    return adapter is not None and adapter.supports(surface)


__all__ = [
    "UPSTREAM_REQUEST_ID_HEADER",
    "CredentialEndpoint",
    "InvalidUpstreamBody",
    "ProviderAdapter",
    "UnsupportedSurface",
    "UpstreamRequest",
    "UpstreamResponse",
    "adapter_for",
    "supports_surface",
]
