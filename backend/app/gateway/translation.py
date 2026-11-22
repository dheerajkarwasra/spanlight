"""Cross-provider request & response translation (OpenAI chat_completions <-> Anthropic messages).

Pure JSON transformations, enabled when `x-spanlight-translate: true` is sent on a gateway
call so routes with heterogeneous fallback targets (OpenAI, Anthropic, Gemini) fail over cleanly.
"""

import json
import uuid
from typing import Any

from app.db.models import ProviderKind
from app.gateway.errors import Surface

DEFAULT_ANTHROPIC_MAX_TOKENS = 4096
_OPENAI_SHAPED = frozenset(
    {ProviderKind.OPENAI, ProviderKind.OPENAI_COMPATIBLE, ProviderKind.GEMINI}
)


def upstream_surface(client_surface: Surface, provider: ProviderKind) -> Surface:
    """The upstream provider's surface matching `client_surface`."""
    if client_surface == "chat_completions" and provider == ProviderKind.ANTHROPIC:
        return "messages"
    if client_surface == "messages" and provider in _OPENAI_SHAPED:
        return "chat_completions"
    return client_surface


def translate_request(
    client_surface: Surface, target_provider: ProviderKind, body: dict[str, Any]
) -> dict[str, Any]:
    """Translate `body` from `client_surface` to `target_provider`'s upstream surface."""
    target = upstream_surface(client_surface, target_provider)
    if target == client_surface:
        return body
    if client_surface == "chat_completions" and target == "messages":
        return _openai_to_anthropic_request(body)
    if client_surface == "messages" and target == "chat_completions":
        return _anthropic_to_openai_request(body)
    return body


def translate_response_body(
    client_surface: Surface,
    upstream: Surface,
    raw_body: bytes,
    fallback_model: str,
) -> bytes:
    """Translate a non-streaming 2xx provider JSON body back into `client_surface` shape."""
    if client_surface == upstream:
        return raw_body
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        return raw_body
    if not isinstance(parsed, dict):
        return raw_body
    if client_surface == "chat_completions" and upstream == "messages":
        return json.dumps(_anthropic_to_openai_response(parsed, fallback_model)).encode("utf-8")
    if client_surface == "messages" and upstream == "chat_completions":
        return json.dumps(_openai_to_anthropic_response(parsed, fallback_model)).encode("utf-8")
    return raw_body


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return "" if content is None else str(content)


def _openai_tool_use_blocks(msg: dict[str, Any], tool_calls: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text = _text_of(msg.get("content"))
    if text:
        blocks.append({"type": "text", "text": text})
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        raw_args = fn.get("arguments")
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except ValueError:
            parsed_args = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name") or "",
                "input": parsed_args if isinstance(parsed_args, dict) else {},
            }
        )
    return blocks


def _openai_to_anthropic_messages(raw_messages: Any) -> tuple[list[str], list[dict[str, Any]]]:
    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    for msg in raw_messages or ():
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            text = _text_of(msg.get("content"))
            if text:
                system_parts.append(text)
        elif role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id") or "",
                            "content": _text_of(msg.get("content")),
                        }
                    ],
                }
            )
        elif role == "assistant" and isinstance(msg.get("tool_calls"), list) and msg["tool_calls"]:
            messages.append(
                {
                    "role": "assistant",
                    "content": _openai_tool_use_blocks(msg, msg["tool_calls"]),
                }
            )
        else:
            messages.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": msg.get("content") or "",
                }
            )
    return system_parts, messages


def _openai_to_anthropic_request(body: dict[str, Any]) -> dict[str, Any]:
    system_parts, messages = _openai_to_anthropic_messages(body.get("messages"))
    out: dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "max_tokens": body.get("max_completion_tokens")
        or body.get("max_tokens")
        or DEFAULT_ANTHROPIC_MAX_TOKENS,
    }
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    for key in ("temperature", "top_p", "stream"):
        if key in body and body[key] is not None:
            out[key] = body[key]
    stop = body.get("stop")
    if isinstance(stop, list | str):
        out["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if isinstance(body.get("tools"), list):
        out["tools"] = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object"}),
            }
            for t in body["tools"]
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        ]
    return out


def _anthropic_to_openai_request(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if body.get("system"):
        messages.append({"role": "system", "content": _text_of(body["system"])})
    for msg in body.get("messages") or ():
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            tool_results = [
                b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            if tool_results:
                for tr in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": _text_of(tr.get("content")),
                        }
                    )
                continue
            if tool_uses:
                text_parts = "".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": text_parts or None,
                        "tool_calls": [
                            {
                                "id": tu.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": tu.get("name", ""),
                                    "arguments": json.dumps(tu.get("input") or {}),
                                },
                            }
                            for tu in tool_uses
                        ],
                    }
                )
                continue
        messages.append({"role": msg.get("role", "user"), "content": _text_of(content)})

    out: dict[str, Any] = {"model": body.get("model"), "messages": messages}
    if body.get("max_tokens") is not None:
        out["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p", "stream"):
        if key in body and body[key] is not None:
            out[key] = body[key]
    if isinstance(body.get("stop_sequences"), list):
        out["stop"] = body["stop_sequences"]
    if isinstance(body.get("tools"), list):
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            }
            for t in body["tools"]
            if isinstance(t, dict)
        ]
    return out


def _anthropic_to_openai_response(body: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    blocks = body.get("content") or ()
    text = "".join(
        b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    )
    tool_calls = [
        {
            "id": b.get("id") or f"call_{i}",
            "type": "function",
            "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input") or {})},
        }
        for i, b in enumerate(blocks)
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    stop = body.get("stop_reason")
    finish = "tool_calls" if stop == "tool_use" else ("length" if stop == "max_tokens" else "stop")
    usage = body.get("usage") or {}
    inp = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return {
        "id": body.get("id") or f"chatcmpl_{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "model": body.get("model") or fallback_model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out},
    }


def _openai_to_anthropic_response(body: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    choices = body.get("choices") or ()
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = first.get("message") or {}
    blocks: list[dict[str, Any]] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": str(msg["content"])})
    for tc in msg.get("tool_calls") or ():
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            try:
                inp_obj = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                inp_obj = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                    "name": fn.get("name") or "",
                    "input": inp_obj if isinstance(inp_obj, dict) else {},
                }
            )
    finish = first.get("finish_reason")
    stop_reason = (
        "tool_use"
        if finish == "tool_calls"
        else ("max_tokens" if finish == "length" else "end_turn")
    )
    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model") or fallback_model,
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }
