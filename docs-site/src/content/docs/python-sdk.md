---
title: Python SDK
description: Install the spanlight package, trace functions and provider calls, and understand what is recorded and how spans are delivered.
sidebar:
  order: 2
---

The `spanlight` package traces LLM applications. It needs Python 3.10 or newer and `httpx`; `openai` and `anthropic` are optional extras.

- **Low overhead.** Tracing never waits on the network: spans are queued in memory and sent in batches from a background thread.
- **Tracing never breaks your app.** SDK failures are logged and swallowed. Exceptions from your code and from the providers are recorded and re-raised unchanged. (`prompts.get` is the one call that can block and raise; see Prompts in the [package README](https://github.com/dheerajkarwasra/spanlight/blob/main/sdks/python/README.md#prompts).)
- **Typed.** The package ships `py.typed` and passes `mypy --strict`.

## Install

```bash
pip install "spanlight[anthropic]"          # or spanlight[openai], or both
```

Plain `pip install spanlight` is enough if you only use `@observe` and `span`. To run the code on `main` instead of the last release:

```bash
pip install "spanlight[anthropic] @ git+https://github.com/dheerajkarwasra/spanlight#subdirectory=sdks/python"
```

## Configure

Create a project in Spanlight, generate an API key under **Settings → API keys**, and give it to the SDK:

```bash
export SPANLIGHT_API_KEY=spl_live_...
export SPANLIGHT_HOST=http://localhost:8080   # your instance
```

```python
import spanlight

spanlight.init(environment="production", release="2026.10.07")
```

Without an API key the SDK logs a warning and runs as a no-op.

| Option | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `api_key` | `SPANLIGHT_API_KEY` | none | Project API key. |
| `host` | `SPANLIGHT_HOST` | `http://localhost:8000` | Base URL of the Spanlight server. Through the Compose stack that is the web port, `http://localhost:8080`. |
| `environment` | `SPANLIGHT_ENVIRONMENT` | none | Attached to every trace, for example `production`. |
| `release` | `SPANLIGHT_RELEASE` | none | Your app version or git SHA. |
| `enabled` | `SPANLIGHT_ENABLED` | `true` | `false`, `0`, `no` or `off` turns the SDK into a no-op. |
| `flush_interval` | | `2.0` | Seconds a span may wait before it is sent. |
| `batch_size` | | `100` | Spans per request (at most 1000). |
| `max_queue` | | `10000` | Buffered spans. When the buffer is full the oldest spans are dropped. |
| `gzip` | | `False` | Gzip-compress request bodies. |
| `timeout` | | `10.0` | HTTP timeout per request, in seconds. |
| `max_retries` | | `5` | Retries for network errors, `429` and `5xx`. |
| `shutdown_timeout` | | `5.0` | Time budget for the flush at interpreter exit. |

## Trace your own code

```python
import spanlight

@spanlight.observe(kind="retrieval")
def search_docs(query: str) -> list[str]:
    return ["Refunds take 5-7 business days."]

@spanlight.observe(name="support-request")
def handle(question: str, user_id: str) -> str:
    spanlight.update_trace(user_id=user_id, session_id="chat-123", tags=["beta"])
    docs = search_docs(question)
    with spanlight.span("format-answer", kind="chain") as span:
        answer = f"From our docs: {docs[0]}"
        span.set_output(answer)
    return answer
```

This produces one trace with three nested spans.

- `@observe(name=None, kind="chain", capture_input=True, capture_output=True)` traces plain functions, `async def`, generators and async generators. `kind` is one of `llm`, `tool`, `retrieval`, `chain`, `http` or `other`. Both `@observe` and `@observe(...)` work.
- `span(name, kind="chain", input=None, attributes=None)` is a context manager (also `async with`) that yields a `Span` with `set_input`, `set_output`, `set_attribute`, `set_attributes`, `set_model`, `set_usage`, `mark_first_token` and `record_error`. An exception that escapes the block marks the span as failed and is re-raised.
- `update_trace(name=None, user_id=None, session_id=None, tags=None)` sets trace-level fields from anywhere inside the trace. Tags accumulate.

Long-running servers need nothing else. Short scripts, CLIs and serverless handlers should call `spanlight.flush()` before they exit; an `atexit` hook also flushes, with a timeout.

## OpenAI and Anthropic

```python
import spanlight
from anthropic import Anthropic
from openai import OpenAI

spanlight.init()
anthropic_client = spanlight.wrap_anthropic(Anthropic())
openai_client = spanlight.wrap_openai(OpenAI())
```

`wrap_anthropic` instruments `messages.create` (including `stream=True`) and `messages.stream` on `Anthropic` and `AsyncAnthropic`. `wrap_openai` instruments `chat.completions.create` and `responses.create` on `OpenAI`, `AsyncOpenAI` and the Azure variants. Streaming works without changes to your code and records time to first token. For OpenAI Chat Completions, ask for usage in the stream with `stream_options={"include_usage": True}`.

If you point the OpenAI client at a compatible API, name the provider yourself: `wrap_openai(client, provider="openrouter")`.

For every LLM call the SDK records the provider, the model (from the response, falling back to the requested one), token usage, time to first token for streams, the request parameters and the assistant message, and the provider's error message when the call fails. API keys, headers and `extra_*` arguments are never recorded.

Token counts follow one rule everywhere: `input_tokens` **includes** cached tokens, and `cached_tokens` says how many of them came from the provider's prompt cache.

## Privacy

The server redacts common secret patterns from payloads on ingestion, and a project can switch payload capture off entirely, so inputs and outputs are dropped. To keep payloads out of the SDK for one function, use `@observe(capture_input=False, capture_output=False)`.

## Delivery guarantees

A batch goes out when it reaches `batch_size` spans or after `flush_interval` seconds. Network errors, `429` and `5xx` are retried with jittered exponential backoff, and `Retry-After` is honoured. Other `4xx` responses are not retried; they are logged to the `spanlight` logger, as are spans the server rejects. Ingestion is idempotent by design: a retried batch upserts the same rows, so it does not double-count. See what the SDK is doing with:

```python
import logging
logging.getLogger("spanlight").setLevel(logging.DEBUG)
```

Threads do not inherit the current span, so copy the context when you hand work to a thread pool:

```python
import contextvars

context = contextvars.copy_context()
executor.submit(context.run, do_work, item)
```

`spanlight.get_current_trace_id()` returns the current trace id so you can put it in your logs.

## Prompts and scores

`spanlight.prompts.get("support/answer")` fetches a prompt version by label from Spanlight, with a cache, a stale copy when the server is unreachable and an optional local fallback, and `spanlight_prompt=compiled` links the span to the version it used. `spanlight.score(trace_id, "helpfulness", 0.8)` attaches a score to a trace. Prompts need an API key with the `prompts:read` scope and scores one with `scores:write`. See [Prompts](/docs/prompts/) and [Scores](/docs/scores/).

## More

The [package README](https://github.com/dheerajkarwasra/spanlight/blob/main/sdks/python/README.md) lists the limitations of the wrappers and links a complete [example](https://github.com/dheerajkarwasra/spanlight/blob/main/sdks/python/examples/support_bot.py).
