"""Unit tests for gateway embeddings summary, Gemini adapter, and cross-provider translation."""

import json
import uuid

from app.db.models import ProviderCredential, ProviderKind
from app.gateway.adapters import adapter_for
from app.gateway.adapters.gemini import GEMINI_API_BASE
from app.gateway.response_summary import summarize_response
from app.gateway.route_config import FallbackPolicy, RetryPolicy, RouteConfig, Target
from app.gateway.routing import compatible_targets
from app.gateway.translation import (
    translate_request,
    translate_response_body,
    upstream_surface,
)


def _cred(provider: ProviderKind, name: str) -> ProviderCredential:
    return ProviderCredential(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        provider=provider,
        name=name,
        ciphertext=b"secret",
        key_id="k1",
        base_url="https://vllm.local/v1" if provider == ProviderKind.OPENAI_COMPATIBLE else None,
    )


def test_embeddings_response_summary_counts_input_tokens_and_zero_output() -> None:
    raw = json.dumps(
        {
            "object": "list",
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 42, "total_tokens": 42},
        }
    ).encode()
    summary = summarize_response("embeddings", raw)
    assert summary.model == "text-embedding-3-small"
    assert summary.usage is not None
    assert summary.usage.input_tokens == 42
    assert summary.usage.output_tokens == 0


def test_gemini_adapter_supports_chat_embeddings_and_models() -> None:
    cred = _cred(ProviderKind.GEMINI, "google-gemini")
    adapter = adapter_for(ProviderKind.GEMINI)
    assert adapter.base_url(cred) == GEMINI_API_BASE
    assert adapter.supports("chat_completions")
    assert adapter.supports("embeddings")
    assert adapter.supports("models")
    req = adapter.prepare(
        cred, "embeddings", {"model": "text-embedding-004", "input": "hi"}, {}, "gk"
    )
    assert req.url == f"{GEMINI_API_BASE}/embeddings"
    assert req.headers["authorization"] == "Bearer gk"


def test_cross_provider_translation_and_routing() -> None:
    anthropic_cred = _cred(ProviderKind.ANTHROPIC, "ant")
    openai_cred = _cred(ProviderKind.OPENAI, "oai")
    route = RouteConfig(
        targets=[Target(credential_id=anthropic_cred.id), Target(credential_id=openai_cred.id)],
        retry=RetryPolicy(),
        fallback=FallbackPolicy(),
    )
    creds = {anthropic_cred.id: anthropic_cred, openai_cred.id: openai_cred}
    assert compatible_targets(route, "chat_completions", creds, translate=False) == [1]
    assert compatible_targets(route, "chat_completions", creds, translate=True) == [0, 1]

    assert upstream_surface("chat_completions", ProviderKind.ANTHROPIC) == "messages"
    ant_req = translate_request(
        "chat_completions",
        ProviderKind.ANTHROPIC,
        {
            "model": "claude-sonnet-5-5",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hi"},
            ],
            "max_completion_tokens": 256,
        },
    )
    assert ant_req["system"] == "Be concise."
    assert ant_req["max_tokens"] == 256
    assert ant_req["messages"] == [{"role": "user", "content": "Hi"}]

    ant_resp = json.dumps(
        {
            "id": "msg_1",
            "model": "claude-sonnet-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hello!"}],
            "usage": {"input_tokens": 10, "output_tokens": 3},
        }
    ).encode()
    translated = json.loads(
        translate_response_body("chat_completions", "messages", ant_resp, "fallback")
    )
    assert translated["object"] == "chat.completion"
    assert translated["choices"][0]["message"]["content"] == "Hello!"
    assert translated["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
