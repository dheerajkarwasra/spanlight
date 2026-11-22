"""Fixed hosts that use OpenAI-shaped chat and model listing."""

from typing import ClassVar

from app.db.models import ProviderKind
from app.gateway.adapters.base import CredentialEndpoint
from app.gateway.adapters.openai import OpenAiAdapter
from app.gateway.errors import Surface


class FixedChatAdapter(OpenAiAdapter):
    provider_headers: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        provider: ProviderKind,
        url: str,
        *,
        chat_stream_usage: bool,
        responses: bool = False,
    ) -> None:
        self.provider = provider
        self._url = url
        self.chat_stream_usage = chat_stream_usage
        paths: dict[Surface, str] = {
            "chat_completions": "/chat/completions",
            "models": "/models",
        }
        if responses:
            paths["responses"] = "/responses"

        self.paths = paths

    def base_url(self, credential: CredentialEndpoint) -> str:
        return self._url
