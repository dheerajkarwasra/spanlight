"""Lone surrogates in prompt templates and variables raise only documented errors.

Failure mode: the byte-size checks encode strictly as UTF-8, so a value such as ``"caf\\udce9"``
(what ``os.fsdecode`` gives for undecodable bytes) raises ``UnicodeEncodeError`` out of
``Prompt.compile`` and ``prompts.get(..., fallback=...)`` instead of a documented error.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from spanlight import InvalidTemplateError, PromptUnavailableError
from spanlight._prompt_client import PromptClient
from spanlight._prompts import fallback_prompt

SURROGATE_FALLBACKS: list[Any] = [
    "Hi \ud800 {{name}}",
    [{"role": "user", "content": "\udc00"}],
]


def test_lone_surrogate_variable_does_not_raise_unicode_error() -> None:
    prompt = fallback_prompt("greet", "Hi {{name}}")

    compiled = prompt.compile(name="caf\udce9")

    assert compiled.text == "Hi caf\udce9"


@pytest.mark.parametrize("fallback", SURROGATE_FALLBACKS)
def test_fallback_with_lone_surrogate_is_a_documented_error(fallback: Any) -> None:
    with pytest.raises(InvalidTemplateError):
        fallback_prompt("greet", fallback).compile(name="x")


@pytest.mark.parametrize("fallback", SURROGATE_FALLBACKS)
def test_get_with_a_lone_surrogate_fallback_is_prompt_unavailable(fallback: Any) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = PromptClient(
        base_url="http://spanlight.test/v1/prompts",
        api_key="spl_live_test",
        timeout=1.0,
        cache_ttl=60.0,
        transport=httpx.MockTransport(unreachable),
    )

    with pytest.raises(PromptUnavailableError, match="fallback is invalid"):
        client.get("greet", fallback=fallback)
