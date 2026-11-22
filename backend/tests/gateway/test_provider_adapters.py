"""Provider wire contracts at the HTTP transport boundary."""

import json
from dataclasses import dataclass

import httpx
import pytest

from app.db.models import ProviderKind
from app.gateway.adapters import adapter_for, supports_surface
from app.gateway.credential_check import probe_models
from app.gateway.credentials import InvalidBaseUrl, base_url_for
from app.gateway.errors import Surface
from app.gateway.schemas import CredentialCreate
from app.gateway.sse import SseFrame
from app.gateway.stream_observers import OpenAiChatObserver

AZURE = "https://my-resource.openai.azure.com/openai/v1"


@dataclass(frozen=True)
class Endpoint:
    base_url: str | None


@pytest.mark.parametrize(
    ("provider", "base", "model", "usage_option"),
    [
        ("azure_openai", AZURE, "my-deployment", True),
        ("mistral", "https://api.mistral.ai/v1", "mistral-small-2603", True),
        ("groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-20b", False),
        ("deepseek", "https://api.deepseek.com", "deepseek-flash", True),
        ("xai", "https://api.x.ai/v1", "grok-4.7", True),
        ("together", "https://api.together.ai/v1", "Qwen/Qwen3.5-9B", False),
    ],
)
async def test_credential_probe_and_inference_use_verified_wire_contract(
    provider: str, base: str, model: str, usage_option: bool
) -> None:
    kind = ProviderKind(provider)
    base_url = AZURE + "/" if kind is ProviderKind.AZURE_OPENAI else None
    assert CredentialCreate(name="provider", provider=kind, api_key="fixture", base_url=base_url)
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        assert await probe_models(http, kind, base_url, "fixture", allow_insecure=False) is None
        adapter = adapter_for(kind)
        surfaces: list[Surface] = ["chat_completions"]
        if provider == "xai":
            surfaces.append("responses")
        for surface in surfaces:
            payload = {"model": model, "stream": True}
            sent = adapter.prepare(
                Endpoint(base_url),
                surface,
                payload,
                {"api-key": "client", "cookie": "client"},
                "fixture",
            )
            response = await adapter.send(http, sent, 2.0)
            await response.aclose()
            assert response.status == 200
    assert [(r.method, str(r.url)) for r in requests] == [
        ("GET", base + "/models"),
        ("POST", base + "/chat/completions"),
        *([("POST", base + "/responses")] if provider == "xai" else []),
    ]
    for request in requests:
        assert request.headers.get("api-key") == ("fixture" if base_url else None)
        assert request.headers.get("authorization") == (None if base_url else "Bearer fixture")
        assert "cookie" not in request.headers
    expected = {"model": model, "stream": True}
    assert json.loads(requests[1].content) == (
        {**expected, "stream_options": {"include_usage": True}} if usage_option else expected
    )
    assert not supports_surface(kind, "embeddings")
    assert not supports_surface(kind, "messages")
    assert supports_surface(kind, "responses") is (provider == "xai")
    if provider == "xai":
        assert json.loads(requests[2].content) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://my-resource.openai.azure.com/openai/v1",
        "https://my-resource.openai.azure.com:0/openai/v1",
        "https://my-resource.openai.azure.com:8443/openai/v1",
        "https://test.my-resource.openai.azure.com/openai/v1",
        "https://my-resource.openai.azure.com.example.org/openai/v1",
        "https://my-resource.openai.azure.com/openai/deployments",
        AZURE + "?api-version=preview",
        AZURE + "#fragment",
    ],
)
async def test_azure_url_policy_applies_to_save_and_probe(url: str | None) -> None:
    with pytest.raises(InvalidBaseUrl):
        base_url_for(ProviderKind.AZURE_OPENAI, url, allow_insecure=True)

    def upstream(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Invalid Azure URL reached transport: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        error = await probe_models(
            http, ProviderKind.AZURE_OPENAI, url, "fixture", allow_insecure=True
        )
    assert error == "base URL not allowed"


def test_groq_reads_its_native_terminal_usage() -> None:
    observer = OpenAiChatObserver(provider=ProviderKind.GROQ)
    observer.on_frame(
        SseFrame(
            None,
            json.dumps(
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "x_groq": {
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 7,
                            "prompt_tokens_details": {"cached_tokens": 0},
                        }
                    },
                }
            ),
        )
    )
    summary = observer.summary()
    assert summary.usage_complete
    assert summary.usage is not None
    assert (
        summary.usage.input_tokens,
        summary.usage.output_tokens,
        summary.usage.cached_tokens,
    ) == (100, 7, 0)
