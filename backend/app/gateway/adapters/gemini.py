"""Google Gemini adapter for native and OpenAI-compatible surfaces."""

from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlencode

from app.db.models import ProviderKind
from app.gateway.adapters.base import CredentialEndpoint, HttpAdapter, UpstreamRequest, strict_json
from app.gateway.errors import Surface, invalid_request
from app.gateway.gemini import valid_model_id

GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_API_BASE = f"{GEMINI_NATIVE_BASE}/openai"


class GeminiAdapter(HttpAdapter):
    chat_stream_usage = True
    provider: ProviderKind = ProviderKind.GEMINI
    paths: Mapping[Surface, str] = {
        "chat_completions": "/chat/completions",
        "embeddings": "/embeddings",
        "models": "/models",
        "gemini_models": "/models",
        "generate_content": "/models",
    }
    provider_headers: ClassVar[frozenset[str]] = frozenset()

    def base_url(self, credential: CredentialEndpoint) -> str:
        return GEMINI_API_BASE

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {"authorization": f"Bearer {api_key}"}

    def prepare(
        self,
        credential: CredentialEndpoint,
        surface: Surface,
        body: Mapping[str, Any],
        client_headers: Mapping[str, str],
        api_key: str,
    ) -> UpstreamRequest:
        if surface not in ("generate_content", "gemini_models"):
            return super().prepare(credential, surface, body, client_headers, api_key)
        headers = self.forward_headers(client_headers)
        headers["x-goog-api-key"] = api_key
        url = f"{GEMINI_NATIVE_BASE}/models"
        if surface == "gemini_models":
            query = urlencode({key: body[key] for key in ("pageSize", "pageToken") if key in body})
            return UpstreamRequest("GET", f"{url}?{query}" if query else url, headers)
        raw_model = body.get("model")
        model = raw_model.removeprefix("models/") if isinstance(raw_model, str) else ""
        if not valid_model_id(model):
            raise invalid_request("Invalid Gemini upstream model identifier.", param="model")
        action = (
            "streamGenerateContent?alt=sse" if body.get("stream") is True else "generateContent"
        )
        headers["content-type"] = "application/json"
        payload = {key: value for key, value in body.items() if key not in ("model", "stream")}
        return UpstreamRequest("POST", f"{url}/{model}:{action}", headers, strict_json(payload))

    def check_request(self, credential: CredentialEndpoint, api_key: str) -> UpstreamRequest:
        return self.prepare(credential, "gemini_models", {}, {}, api_key)
