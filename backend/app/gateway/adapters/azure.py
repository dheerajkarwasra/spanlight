"""Azure OpenAI v1 Chat Completions and model discovery."""

from collections.abc import Mapping

from app.db.models import ProviderKind
from app.gateway.adapters.base import CredentialEndpoint, HttpAdapter
from app.gateway.credentials import azure_base_url
from app.gateway.errors import Surface


class AzureOpenAiAdapter(HttpAdapter):
    provider: ProviderKind = ProviderKind.AZURE_OPENAI
    paths: Mapping[Surface, str] = {
        "chat_completions": "/chat/completions",
        "models": "/models",
    }
    chat_stream_usage = True

    def base_url(self, credential: CredentialEndpoint) -> str:
        return azure_base_url(credential.base_url)

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {"api-key": api_key}
