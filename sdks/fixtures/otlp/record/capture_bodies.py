"""Record the provider response bodies the fake provider replays. Owner-run, never in CI.

    ANTHROPIC_API_KEY=... OPENAI_API_KEY=... uv run python capture_bodies.py --max-usd 0.05
    uv run python capture_bodies.py --max-usd 0.05 --dry-run        # plan and worst case only
    ... --openai                                 # also re-record the OpenAI chat/responses bodies

By default it records what no fixture in the tree holds: an Anthropic prompt-cache write and the
cache read of the same prompt, an Anthropic stream, and one OpenAI `text-embedding-3-small`
call. `--openai` adds the Chat Completions and Responses scenarios that today are constructed by
hand. Bodies are written verbatim (bytes as received, ids included) to `responses/<provider>/`
with a `<scenario>.provenance.txt` sidecar; the harness never edits usage numbers.

Spend is capped: `--max-usd` is required and at most `HARD_CAP_USD`. Before each call the worst
case (prompt priced at the cache-write rate plus `max_tokens` priced as output) is checked against
the cap; after each call the real cost from the returned usage is added. Keys come from the
environment only; a body containing a key-shaped string aborts the run.
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

HARD_CAP_USD = Decimal("0.10")
OUT_DIR = Path(__file__).resolve().parent / "responses"
ANTHROPIC_URL = "https://api.anthropic.com"
OPENAI_URL = "https://api.openai.com"
ANTHROPIC_MODEL = "claude-haiku-4-5"
OPENAI_MODEL = "gpt-4.1-mini"
EMBEDDING_MODEL = "text-embedding-3-small"
TIMEOUT_SECONDS = 60.0
MILLION = Decimal(1_000_000)
# USD per million tokens: input, output, cache write, cache read. Mirrors the app's seed prices.
PRICES = {
    ANTHROPIC_MODEL: (Decimal("1.00"), Decimal("5.00"), Decimal("1.25"), Decimal("0.10")),
    OPENAI_MODEL: (Decimal("0.40"), Decimal("1.60"), Decimal("0.40"), Decimal("0.10")),
    EMBEDDING_MODEL: (Decimal("0.02"), Decimal(0), Decimal("0.02"), Decimal("0.02")),
}
_RATE_INDEX = {
    "input_tokens": 0,
    "prompt_tokens": 0,
    "output_tokens": 1,
    "completion_tokens": 1,
    "cache_creation_input_tokens": 2,
    "cache_read_input_tokens": 3,
}
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
# A scenario is saved only when its usage proves what the file name claims.
REQUIRED_USAGE = {
    "messages_cache_write": "cache_creation_input_tokens",
    "messages_cache_read": "cache_read_input_tokens",
}

PROMPT = "Say hello in five words."
# Haiku 4.5 caches prefixes of 4,096 tokens or more; ~5,500 tokens leaves a margin.
LONG_SYSTEM = " ".join(
    f"Clause {i}: the assistant answers briefly, in plain words, and never guesses a number."
    for i in range(1, 301)
)
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


@dataclass(frozen=True)
class Call:
    provider: str
    scenario: str
    path: str
    body: dict[str, Any]
    max_output_tokens: int

    @property
    def stream(self) -> bool:
        return self.body.get("stream") is True

    @property
    def filename(self) -> str:
        return f"{self.scenario}{'.sse' if self.stream else '.json'}"


def anthropic_calls() -> list[Call]:
    body: dict[str, Any] = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 16,
        "system": [{"type": "text", "text": LONG_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": PROMPT}],
    }
    return [
        Call("anthropic", "messages_cache_write", "/v1/messages", body, 16),
        Call("anthropic", "messages_cache_read", "/v1/messages", body, 16),
        Call("anthropic", "messages_stream", "/v1/messages", {**body, "stream": True}, 16),
    ]


def openai_calls(*, recapture: bool) -> list[Call]:
    embed = {"model": EMBEDDING_MODEL, "input": "The quick brown fox jumps over the lazy dog."}
    calls = [Call("openai", "embeddings", "/v1/embeddings", embed, 0)]
    if not recapture:
        return calls
    chat: dict[str, Any] = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_completion_tokens": 32,
    }
    tools = {
        **chat,
        "messages": [{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
        "tools": [WEATHER_TOOL],
        "tool_choice": "required",
        "max_completion_tokens": 64,
    }
    responses = {"model": OPENAI_MODEL, "input": PROMPT, "max_output_tokens": 32}
    stream_options = {"stream": True, "stream_options": {"include_usage": True}}
    return [
        *calls,
        Call("openai", "chat", "/v1/chat/completions", chat, 32),
        Call("openai", "chat_tools", "/v1/chat/completions", tools, 64),
        Call("openai", "chat_stream", "/v1/chat/completions", {**chat, **stream_options}, 32),
        Call("openai", "responses", "/v1/responses", responses, 32),
        Call("openai", "responses_stream", "/v1/responses", {**responses, "stream": True}, 32),
    ]


def worst_case_usd(call: Call) -> Decimal:
    """Prompt at ~3 characters per token priced as a cache write, plus the full output budget."""
    _, output, write, _ = PRICES[call.body["model"]]
    prompt_tokens = len(json.dumps(call.body)) // 3
    return (prompt_tokens * write + call.max_output_tokens * output) / MILLION


def _usage_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        if isinstance(obj.get("usage"), dict):
            yield obj["usage"]
        for value in obj.values():
            yield from _usage_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _usage_dicts(value)


def usage_from(raw: bytes, *, stream: bool) -> dict[str, int]:
    """Merge every usage object in a body (or SSE frames) by taking the largest value per key.

    Streams repeat usage across events (Anthropic sends input tokens on `message_start` and
    output tokens on `message_delta`), so the maximum per key is the final count.
    """
    if stream:
        frames = [line[5:] for line in raw.decode().splitlines() if line.startswith("data:")]
        parsed = [json.loads(frame) for frame in frames if frame.strip() not in ("", "[DONE]")]
    else:
        parsed = [json.loads(raw)]
    merged: dict[str, int] = {}
    for usage in _usage_dicts(parsed):
        for key, value in usage.items():
            if isinstance(value, int) and key in _RATE_INDEX:
                merged[key] = max(merged.get(key, 0), value)
    return merged


def cost_usd(model: str, usage: dict[str, int]) -> Decimal:
    rates = PRICES[model]
    return (
        sum((usage[k] * rates[i] for k, i in _RATE_INDEX.items() if k in usage), Decimal(0))
        / MILLION
    )


def _headers(call: Call, key: str) -> dict[str, str]:
    auth = (
        {"x-api-key": key, "anthropic-version": "2023-06-01"}
        if call.provider == "anthropic"
        else {"authorization": f"Bearer {key}"}
    )
    # The fake provider routes on this header; the real APIs ignore it.
    return {**auth, "content-type": "application/json", "x-fixture-scenario": call.scenario}


def record(call: Call, base_url: str, key: str, out: Path) -> Decimal:
    response = httpx.post(
        base_url + call.path, headers=_headers(call, key), json=call.body, timeout=TIMEOUT_SECONDS
    )
    if response.status_code != 200:
        # Status only: a provider's error body can echo the key it rejected.
        sys.exit(f"{call.scenario}: HTTP {response.status_code}; nothing saved")
    raw = response.content
    if _KEY_PATTERN.search(raw.decode(errors="replace")):
        sys.exit(f"{call.scenario}: the response contains a key-shaped string; not saved")
    usage = usage_from(raw, stream=call.stream)
    required = REQUIRED_USAGE.get(call.scenario)
    if required and usage.get(required, 0) <= 0:
        sys.exit(f"{call.scenario}: expected {required} > 0 but the usage was {usage}; not saved")
    cost = cost_usd(call.body["model"], usage)
    target = out / call.provider / call.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    target.with_suffix(".provenance.txt").write_text(
        f"recorded {stamp} by capture_bodies.py: POST {call.path}, model {call.body['model']}, "
        f"usage {json.dumps(usage, sort_keys=True)}, cost {cost:.6f} USD\n"
    )
    return cost


def _usd(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not Decimal(0) < value <= HARD_CAP_USD:
        raise argparse.ArgumentTypeError(f"must be between 0 and {HARD_CAP_USD}")
    return value


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-usd", type=_usd, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--openai", action="store_true", help="also re-record the OpenAI scenarios")
    parser.add_argument("--scenarios", help="comma-separated subset of scenario names")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--anthropic-url", default=ANTHROPIC_URL)
    parser.add_argument("--openai-url", default=OPENAI_URL)
    return parser.parse_args()


def main() -> None:
    args = _args()
    if os.environ.get("CI"):
        sys.exit("refusing to record under CI: this script spends money and needs real keys")
    calls = [*anthropic_calls(), *openai_calls(recapture=args.openai)]
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        calls = [c for c in calls if c.scenario in wanted]
        if len(calls) != len(wanted):
            sys.exit(f"unknown scenario in {sorted(wanted)}; known: {[c.scenario for c in calls]}")
    urls = {"anthropic": args.anthropic_url, "openai": args.openai_url}
    key_envs = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

    spent = Decimal(0)
    for call in calls:
        worst = worst_case_usd(call)
        if spent + worst > args.max_usd:
            sys.exit(
                f"{call.scenario}: worst case {worst:.6f} USD on top of {spent:.6f} spent would "
                f"pass the cap of {args.max_usd} USD; stopped before calling"
            )
        if args.dry_run:
            print(f"{call.provider}/{call.filename}: worst case {worst:.6f} USD")
            spent += worst
            continue
        key = os.environ.get(key_envs[call.provider])
        if not key:
            sys.exit(f"{call.scenario}: set {key_envs[call.provider]}")
        cost = record(call, urls[call.provider], key, args.out)
        spent += cost
        print(f"{call.provider}/{call.filename}: recorded, cost {cost:.6f} USD")
    label = "worst case total" if args.dry_run else "spent"
    print(f"{label} {spent:.6f} USD over {len(calls)} calls")


if __name__ == "__main__":
    main()
