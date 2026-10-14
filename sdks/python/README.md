# Spanlight Python SDK

Tracing for LLM applications. Instrument your code with a decorator, wrap your
OpenAI or Anthropic client, and every request shows up in
[Spanlight](https://github.com/dheerajkarwasra/spanlight/blob/main/README.md) with latency, tokens, cost, errors and the
full prompt and completion.

- **Low overhead.** Tracing never waits on the network: spans are queued in
  memory and sent in batches from a background thread.
- **Tracing never breaks your app.** SDK failures are logged and swallowed.
  Exceptions from your code and from the providers are recorded and re-raised
  exactly as they were. (`prompts.get` is the one call that can block and
  raise; see [Prompts](#prompts).)
- **One dependency.** Only `httpx` is required. `openai` and `anthropic` are
  optional extras.
- **Typed.** Ships `py.typed` and passes `mypy --strict`.

## Install

```bash
pip install "spanlight[anthropic]"
```

The core package needs only `httpx`. Add the extra for the provider you use:
`spanlight[openai]`, `spanlight[anthropic]` or `spanlight[openai,anthropic]`.
Plain `pip install spanlight` is enough if you only use `@observe` and `span`.
With uv, run `uv add "spanlight[anthropic]"`.

To run the code on `main` instead of the last release, install from the
repository's `sdks/python` subdirectory:

```bash
pip install "spanlight[anthropic] @ git+https://github.com/dheerajkarwasra/spanlight#subdirectory=sdks/python"
```

Python 3.10 or newer is required.

## Quickstart

Create a project in Spanlight, generate an API key under
**Settings → API keys**, and export it:

```bash
export SPANLIGHT_API_KEY=spl_live_...
export SPANLIGHT_HOST=http://localhost:8000   # or your hosted instance
```

```python
import spanlight

spanlight.init(environment="production", release="2026.10.07")


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


handle("How long do refunds take?", user_id="u_42")
```

This produces one trace with three nested spans: `support-request`,
`search_docs` and `format-answer`. Long-running servers need nothing else.
Short scripts, CLIs and serverless handlers should call `spanlight.flush()`
before they exit. An `atexit` hook also flushes, with a timeout, when the
interpreter shuts down.

## OpenAI

```python
import spanlight
from openai import OpenAI

spanlight.init()
client = spanlight.wrap_openai(OpenAI())


@spanlight.observe
def answer(question: str) -> str:
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": question}],
    )
    return completion.choices[0].message.content or ""
```

`wrap_openai` instruments `chat.completions.create` and `responses.create` on
the client it is given. It works with `OpenAI`, `AsyncOpenAI` and the Azure
variants. The SDK's own `.stream()` helpers are traced as well, because they
call `create` internally.

Streaming works without any changes to your code. Usage is recorded when
OpenAI sends it; for Chat Completions that means opting in:

```python
stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Tell me a joke"}],
    stream=True,
    stream_options={"include_usage": True},  # include token usage in the stream
)
for chunk in stream:
    ...
```

The span ends when the stream is exhausted or closed, and records
time-to-first-token. If you point the OpenAI SDK at a compatible API, set the
provider name explicitly: `wrap_openai(client, provider="openrouter")`.

## Anthropic

```python
import spanlight
from anthropic import Anthropic

spanlight.init()
client = spanlight.wrap_anthropic(Anthropic())

message = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=512,
    messages=[{"role": "user", "content": "Summarize our refund policy."}],
)

# The streaming helper is traced too, including time-to-first-token.
with client.messages.stream(
    model="claude-haiku-4-5",
    max_tokens=512,
    messages=[{"role": "user", "content": "Write a haiku about latency."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="")
```

`wrap_anthropic` instruments `messages.create` (including `stream=True`) and
`messages.stream` on both `Anthropic` and `AsyncAnthropic`.

## Prompts

Keep prompts in Spanlight and fetch them by label, so a new version ships
without a deploy. The API key needs the `prompts:read` scope.

```python
import spanlight
from openai import OpenAI

spanlight.init()
client = spanlight.wrap_openai(OpenAI())

prompt = spanlight.prompts.get(
    "support/answer",  # label "production" by default
    fallback=[{"role": "user", "content": "Answer briefly: {{question}}"}],
)
compiled = prompt.compile(question="How do refunds work?")

completion = client.chat.completions.create(
    model=prompt.model_config.get("model", "gpt-4o-mini"),
    messages=compiled.messages,
    spanlight_prompt=compiled,  # links the span to this prompt version
)
```

`get(name, label="production", *, version=None, fallback=None)` returns a
`Prompt` with `name`, `version`, `version_id`, `kind` (`text` or `chat`),
`template`, `variables` and `model_config`. Pass `version=3` to pin a number
instead of a label.

- **Caching.** A fetched prompt is served from memory for `prompt_cache_ttl`
  seconds (60 by default), then revalidated with `If-None-Match`, so an
  unchanged prompt costs a `304`. Concurrent first calls for the same prompt
  make one request.
- **Outages.** After the TTL, if the server cannot be reached or answers with
  an error, the cached copy keeps being served and one warning per prompt per
  minute is logged. With nothing cached, `fallback` (a text template or a list
  of `{"role", "content"}` messages) is used and the prompt has
  `version=None`. With neither, `get` raises `PromptUnavailableError`, the one
  exception the SDK raises on purpose. A failed fetch is remembered for a few
  seconds, so concurrent callers do not each wait out the timeout, and while
  one caller revalidates an expired prompt the others get the cached copy.
- **Disabled SDK.** `enabled=False` turns off tracing and scores, but
  `prompts.get` still calls the server when an API key is set. Pass no key (and
  a `fallback`) to keep tests offline.
- **Blocking.** `get` is synchronous. In `async` code a cache miss blocks the
  event loop for up to `timeout`; call it at startup or through
  `asyncio.to_thread`.

`Prompt.compile(**variables)` fills in `{{name}}` variables (spaces inside the
braces are allowed; values are inserted as they are and never expanded again).
Values are strings; numbers and booleans are converted with `str()`, and any
other type raises `TypeError`.
The result has `text` for a text prompt, `messages` (plain dicts for the OpenAI
and Anthropic SDKs) for a chat prompt, and `unused_variables` for variables you
passed that the template does not use. A missing variable raises
`MissingVariablesError` naming all of them; a malformed template raises
`InvalidTemplateError`.

To link spans to a prompt version, pass `spanlight_prompt=compiled` to a
wrapped OpenAI or Anthropic call (the keyword is removed before the provider
sees it), or enter the compiled prompt; every span started inside the block is
linked:

```python
with compiled:
    answer = run_agent(compiled.messages)
```

The link is a `contextvars` value, so it follows the same rules as the current
span: an `asyncio` task created inside the block keeps the link for its whole
life, and a thread pool worker does not see it unless you copy the context.
Start long-lived work inside `with spanlight.no_prompt():` to keep its spans
unlinked. Passing a fallback (`version=None`) as `spanlight_prompt=` clears the
link for that call.

## Scores

Attach quality scores to a trace, or to one of its spans, from your code:

```python
trace_id = spanlight.get_current_trace_id()
spanlight.score(trace_id, "helpfulness", 0.8)
spanlight.score(trace_id, "resolved", True, comment="user said thanks")
spanlight.score(trace_id, "tone", "friendly", span_id=span.span_id)
```

A number makes a numeric score, `True`/`False` a boolean score and a string a
categorical one. Scores are sent in the background with the spans, after the
spans of the same batch, and each gets a unique id so a retried request never
stores one twice. `score` never raises. The API key needs the `scores:write`
scope; a server that predates scores answers `404`, which is logged once.

## Experiments in CI

The SDK includes the runner behind the `eval-gate` GitHub Action. It runs a
prompt version over a dataset on the server, scores the answers with your
evaluators and exits non-zero when a score is below its threshold:

```bash
export SPANLIGHT_TOKEN=spl_pat_...   # a personal access token with the write scope
python -m spanlight._experiments \
  --host https://spanlight.example.com --project-id <project id> \
  --dataset support-questions --prompt support/answer@production \
  --evaluators relevance,format --fail-below relevance=0.8,format=1 \
  --credential-id <credential id> --model claude-haiku-5-5
```

`--prompt` is `name`, `name@label` or `name:version`. Also pass `--credential-id`
and `--model` (with the evaluators they select the experiment), and optionally
`--max-cost-usd` to cap a new experiment's runs. Exit code `0` means
every threshold held, `1` a score was below its threshold and `2` the run could
not be set up, failed or timed out (`--timeout`, 1800 seconds by default). The
host must be `https://` unless it is localhost. Each
run makes real provider calls. In a GitHub workflow use the
`.github/actions/eval-gate` Action instead of calling the module yourself.

## What gets recorded

For every LLM call:

| Field                       | Source                                                                                                                            |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `provider`                  | `openai` / `anthropic`                                                                                                            |
| `model`                     | Model from the response (for example `gpt-4o-mini-2024-07-18`). Falls back to the requested model.                                |
| `usage.input_tokens`        | Total prompt tokens, **including** cached tokens. For Anthropic this is input + cache reads + cache writes.                       |
| `usage.output_tokens`       | Completion tokens                                                                                                                 |
| `usage.cached_tokens`       | Prompt tokens served from the provider's prompt cache                                                                             |
| `time_to_first_token_ms`    | Streaming calls only                                                                                                              |
| `input`                     | Request parameters such as `messages`, `system` and `temperature`. API keys, headers, `extra_*` and `timeout` are never recorded. |
| `output`                    | The assistant message, including tool calls                                                                                       |
| `status` / `status_message` | `error` plus the provider's message when the call fails                                                                           |

The Spanlight server also redacts common secret patterns from
payloads on ingestion. Set `capture_payloads = false` on a project to drop
inputs and outputs entirely. To keep payloads out of the SDK side for a
specific function, use `@observe(capture_input=False, capture_output=False)`.

## API reference

The full API, with every signature, default and error, is in the
[Python SDK reference](https://github.com/dheerajkarwasra/spanlight/blob/main/docs-site/src/content/docs/reference/python-sdk.mdx).
The sections below are a summary.

### `init(**options) -> Spanlight`

Configures the global client used by `observe`, `span` and the wrappers.
Calling it again replaces the previous client after flushing it.
`tracer = Spanlight(**options)` creates an independent client with the same
options. Use `tracer.observe`, `tracer.span` and
`wrap_openai(client, spanlight=tracer)` to report through it.

| Option             | Environment variable    | Default                 | Meaning                                                                  |
| ------------------ | ----------------------- | ----------------------- | ------------------------------------------------------------------------ |
| `api_key`          | `SPANLIGHT_API_KEY`     | none                    | Project API key. Without one the SDK logs a warning and runs as a no-op. |
| `host`             | `SPANLIGHT_HOST`        | `http://localhost:8000` | Base URL of the Spanlight API                                            |
| `environment`      | `SPANLIGHT_ENVIRONMENT` | none                    | Attached to every trace, for example `production`                        |
| `release`          | `SPANLIGHT_RELEASE`     | none                    | Your app version or git SHA                                              |
| `enabled`          | `SPANLIGHT_ENABLED`     | `true`                  | `false`/`0`/`no`/`off` turns tracing and scores into a no-op             |
| `flush_interval`   |                         | `2.0`                   | Seconds a span may wait before it is sent                                |
| `batch_size`       |                         | `100`                   | Spans per request (max 1000)                                             |
| `max_queue`        |                         | `10000`                 | Buffered spans. When the buffer is full, the oldest spans are dropped.   |
| `gzip`             |                         | `False`                 | Gzip-compress request bodies                                             |
| `timeout`          |                         | `10.0`                  | HTTP timeout per request, in seconds                                     |
| `max_retries`      |                         | `5`                     | Retries for network errors, 429 and 5xx                                  |
| `shutdown_timeout` |                         | `5.0`                   | Time budget for the flush at interpreter exit                            |
| `prompt_cache_ttl` |                         | `60.0`                  | Seconds a fetched prompt is served before it is revalidated              |

### `@observe(name=None, kind="chain", capture_input=True, capture_output=True)`

Traces each call of a function. It supports plain functions, `async def`,
generators and async generators. For generators, the span stays open until
iteration finishes or the generator is closed, and the yielded items become
the output (strings are joined). `kind` is one of `llm`, `tool`, `retrieval`,
`chain`, `http` or `other`. Both `@observe` and `@observe(...)` forms work.

### `span(name, kind="chain", input=None, attributes=None)`

A context manager for `with` or `async with` that yields a `Span`:

```python
with spanlight.span("rerank", kind="retrieval") as span:
    span.set_input({"candidates": 50})
    span.set_attribute("reranker", "bge-v2")
    span.set_output(top_k)
```

`Span` methods: `set_input`, `set_output`, `set_attribute`, `set_attributes`,
`set_model(model, provider=...)`, `set_usage(input_tokens=, output_tokens=,
cached_tokens=)`, `set_finish_reason(reason)`, `set_request_hash(hash)`,
`mark_first_token()` and `record_error(exc_or_message)`. An
exception that escapes the block marks the span as failed and is re-raised
unchanged.

### `update_trace(name=None, user_id=None, session_id=None, tags=None)`

Sets trace-level fields on the trace of the current span. You can call it
anywhere inside the trace. Tags accumulate across calls.

### `flush(timeout=None) -> bool` and `shutdown(timeout=None)`

`flush` blocks until every span ended and score recorded so far has been sent. `shutdown` flushes
and then stops the background thread.

### Context propagation

The current span is stored in a `contextvars.ContextVar`. Nested calls and
`asyncio` tasks pick it up automatically. Threads do not inherit context, so
copy it explicitly when you hand work to a thread pool:

```python
import contextvars

context = contextvars.copy_context()
executor.submit(context.run, do_work, item)
```

Trace ids are 32 lowercase hex characters and span ids are 16, both
W3C Trace Context compatible. `spanlight.get_current_trace_id()` returns the
current trace id so you can put it in your logs.

## Delivery guarantees

Spans are sent by a background daemon thread. A batch goes out when it reaches
`batch_size` spans or after `flush_interval` seconds. Failed requests are
retried with jittered exponential backoff on network errors, `429` and `5xx`,
and `Retry-After` is honored. Other `4xx` responses are not retried. They are
logged to the `spanlight` logger, as are any spans the server rejects. If the
queue fills up, the oldest spans are dropped and counted
(`spanlight.get_client().exporter.dropped_count`).

To see what the SDK is doing:

```python
import logging

logging.getLogger("spanlight").setLevel(logging.DEBUG)
```

## Example

[`examples/support_bot.py`](https://github.com/dheerajkarwasra/spanlight/blob/main/sdks/python/examples/support_bot.py) is a small three-step
support bot (classify, retrieve, answer with streaming). It runs against
Anthropic or OpenAI, whichever key you have set:

```bash
export SPANLIGHT_API_KEY=spl_live_... ANTHROPIC_API_KEY=sk-ant-...
uv run --extra anthropic --extra openai python examples/support_bot.py "How do refunds work?"
```

## Development

```bash
cd sdks/python
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run pytest
```

The wrapper tests run the real `openai` and `anthropic` SDKs against an
in-process mock HTTP transport. The mock returns recorded-shape JSON and SSE
responses, so no network access or API keys are needed.

## Limitations

- The wrappers instrument the client instance you pass in. Calls made through
  `.with_raw_response` / `.with_streaming_response` are passed through
  untraced.
- Wrapped streaming calls return a transparent proxy, not the provider's
  `Stream` class, so `isinstance(result, openai.Stream)` is `False`. Iteration,
  context-manager use, `close()` and attributes such as `.response` all
  behave as before.
- If a stream is abandoned without being exhausted or closed, its span is
  never sent.
