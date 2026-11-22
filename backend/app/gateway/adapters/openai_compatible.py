"""OpenAI-compatible servers (vLLM, Ollama, proxies): the OpenAI adapter on the credential's URL.

The base URL was validated when the credential was saved, and every connection is vetted again
by the upstream client, so a URL that the policy no longer allows fails at call time.
"""

from app.db.models import ProviderKind
from app.gateway.adapters.base import CredentialEndpoint
from app.gateway.adapters.openai import OpenAiAdapter


class OpenAiCompatibleAdapter(OpenAiAdapter):
    provider: ProviderKind = ProviderKind.OPENAI_COMPATIBLE

    def base_url(self, credential: CredentialEndpoint) -> str:
        if credential.base_url is None:
            # The migration's CHECK makes this unreachable for a stored row.
            raise ValueError("an OpenAI-compatible credential needs a base URL")
        return credential.base_url
