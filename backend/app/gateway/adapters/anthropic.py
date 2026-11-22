"""Anthropic: messages and the model list, with an `x-api-key` and an API version header."""

from collections.abc import Mapping
from typing import ClassVar

from app.db.models import ProviderKind
from app.gateway.adapters.base import CredentialEndpoint, HttpAdapter
from app.gateway.errors import Surface

ANTHROPIC_API_BASE = "https://api.anthropic.com"
# Sent when the client does not choose one; the client's own value passes through unchanged.
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(HttpAdapter):
    provider: ProviderKind = ProviderKind.ANTHROPIC
    paths: Mapping[Surface, str] = {
        "messages": "/v1/messages",
        "models": "/v1/models",
    }
    provider_headers: ClassVar[frozenset[str]] = frozenset({"anthropic-version", "anthropic-beta"})
    default_headers: ClassVar[Mapping[str, str]] = {"anthropic-version": ANTHROPIC_VERSION}

    def base_url(self, credential: CredentialEndpoint) -> str:
        return ANTHROPIC_API_BASE

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {"x-api-key": api_key}
