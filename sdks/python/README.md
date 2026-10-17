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
variants; an Azure client is recorded with the provider `azure_openai`, which the
server does not price at OpenAI's rates (add a price override for it). The SDK's own `.stream()` helpers are traced as well, because they
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

When a stream carries no usage, the span's output token count is left unknown
(never guessed) and the `usage.unreported_reason` attribute says why:
`include_usage_not_set` when a Chat Completions stream ended without a usage
chunk, `no_final_usage` when a Responses stream ended without its terminal
event, `client_disconnected` when your code closed it early, and
`stream_error` when it failed. Such a call's cost shows as unknown.

### Service tier and web search

- The `service_tier` OpenAI answers with (on the response, or on the last
  stream chunk or terminal event) is recorded as `standard`, `flex`, `priority`
  or `batch`; `default`, `auto` and `scale` are `standard`. A value the SDK does
  not know is kept in the `usage.service_tier_raw` attribute and the call is
  priced at the standard rate.
- For the Responses API, every completed `web_search_call` output item counts
  as one billed web search; searches still in progress or failed are not
  counted.
- Chat Completions with `web_search_options` report sources as message
  annotations, which are not a count of searches. No count is recorded and the
  span gets `usage.fee_unknown = "openai_chat_web_search"`, so the search fee
  shows as unknown rather than zero.
- Requests that set `inference_geo` (other than `global`) or `speed: "fast"`
  are priced at the standard rate and tagged with
  `usage.pricing_modifier_unknown`, because those modifiers are not modelled.

### Batch output

OpenAI Batch results are a file you download, so record them explicitly once
the batch has completed:

```python
batch = client.batches.retrieve("batch_67ccd2bed1ec8190b14f964abc054267")
content = client.files.content(batch.output_file_id)
recorded = spanlight.record_openai_batch_output(content.iter_lines(), batch_id=batch.id)
```

Pass the error file (`batch.error_file_id`) the same way to record failed
requests. Each line becomes one `llm` span with the shape described under
[Anthropic batch results](#batch-results): `service_tier` is `batch` (the tier
the body reported is kept in `usage.service_tier_raw`), cached tokens come from
the usage details, and a line whose `status_code` is not 200 or that carries an
`error` becomes an error span without usage. A Responses body with
`status: "failed"` becomes an error span that keeps the usage OpenAI reports
(it is billed), and `status: "incomplete"` a normal span whose finish reason is
`incomplete_details.reason`. Chat Completions and Responses
bodies are both read. The function accepts any iterable of `str` or `bytes`
lines (or the whole file as one string), returns the number of spans recorded,
and never raises: unreadable lines are skipped. Use `provider=` for a
compatible API and `spanlight=` for a client other than the global one.

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

### Batch results

Results read through `messages.batches.results` are recorded as you iterate
them, on both `Anthropic` and `AsyncAnthropic`:

```python
for item in client.messages.batches.results("msgbatch_01HkcTjaV5uDC8jWR4ZsDV8d"):
    handle(item)  # each item is yielded unchanged
```

Each result becomes one `llm` span named `batch <model>`, in a trace named
`batch <batch_id>` and tagged `spanlight:batch`:

- A `succeeded` result carries the model, token usage, finish reason and the
  response content, with `service_tier` set to `batch` (the Batches API bills
  every request at the batch rate). The tier the response reported is kept in
  the `usage.service_tier_raw` attribute.
- `errored`, `canceled` and `expired` results are error spans without usage,
  because they are not billed.
- The request is not part of a result, so the span has no input
  (`batch.request_available` is `false`). Its start and end are both the
  moment the result was read (`timing.synthetic` is `true`).
- The attributes `batch.id`, `batch.custom_id` and `batch.result_type` link
  the span back to the batch.
- Trace and span ids are derived from the batch id and `custom_id`, so reading
  the same batch again updates the same spans instead of adding new ones.

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

The run prints a Markdown report (also appended to the GitHub step summary): the
run's items, errors, cost and latency, then one row per score with its mean, the
95% confidence interval on the pass rate of a boolean score (for example
`64–88%`; `—` for numeric and categorical scores, and from servers that predate
intervals), the number of items, the threshold and the result. When a threshold
lies inside a boolean score's interval, the report adds "Interval includes the
threshold; the result is not conclusive at this sample size." The gate itself
still compares the mean with the threshold, so the exit codes do not depend on
the interval.

## What gets recorded

For every LLM call:

| Field                         | Source                                                                                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `provider`                    | `openai` / `anthropic`                                                                                                                                                    |
| `model`                       | Model from the response (for example `gpt-4o-mini-2024-07-18`). Falls back to the requested model.                                                                        |
| `usage.input_tokens`          | Total prompt tokens, **including** cached tokens. For Anthropic this is input + cache reads + cache writes.                                                               |
| `usage.output_tokens`         | Completion tokens                                                                                                                                                         |
| `usage.cached_tokens`         | Prompt tokens served from the provider's prompt cache                                                                                                                     |
| `usage.cache_write_tokens`    | Prompt tokens written to the prompt cache (Anthropic `cache_creation_input_tokens`). Sent only when the provider reports it.                                              |
| `usage.cache_write_1h_tokens` | The part of `cache_write_tokens` written with Anthropic's 1-hour TTL (`cache_creation.ephemeral_1h_input_tokens`); the rest are 5-minute writes. Sent only when reported. |
| `time_to_first_token_ms`      | Streaming calls only                                                                                                                                                      |
| `input`                       | Request parameters such as `messages`, `system` and `temperature`. API keys, headers, `extra_*` and `timeout` are never recorded.                                         |
| `output`                      | The assistant message, including tool calls                                                                                                                               |
| `status` / `status_message`   | `error` plus the provider's message when the call fails                                                                                                                   |

When a provider call fails with an HTTP error, the span also carries the
provider's error type and code (`provider.error.type`, `provider.error.code`),
the wait it asked for in seconds (`provider.retry_after_s`, from `retry-after-ms`
or a numeric `retry-after`), and `provider.ratelimit`: the response's rate-limit
headers, filtered through a fixed allowlist of 29 names. Only `retry-after`,
`retry-after-ms`, OpenAI's `x-ratelimit-*` headers and Anthropic's
`anthropic-ratelimit-*` and `anthropic-priority-*` headers are kept, each value
at most 64 printable ASCII characters. Every other response header, including
`authorization`, `x-api-key`, `cookie`, `set-cookie` and Anthropic's
organization and workspace ids, is dropped before the span leaves your process.
Connection errors and timeouts add none of these fields.
When the Spanlight gateway refused the call itself (its `X-Spanlight-Code`
header names one of its own codes, such as `RATE_LIMITED` for your gateway key's
limit), the span carries that code as `spanlight.gateway.error_code` instead, and
none of the `provider.*` fields above.

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
cached_tokens=, cache_write_tokens=, cache_write_1h_tokens=)`,
`set_finish_reason(reason)`, `set_request_hash(hash)`,
`mark_first_token()` and `record_error(exc_or_message)`. An
exception that escapes the block marks the span as failed and is re-raised
unchanged.

### `update_trace(name=None, user_id=None, session_id=None, tags=None, metadata=None)`

Sets trace-level fields on the trace of the current span. You can call it
anywhere inside the trace. Tags accumulate across calls. Metadata keys
accumulate too, and a later value replaces a key's earlier one:
`spanlight.update_trace(metadata={"customer_id": "acme", "feature": "search"})`.
Up to 32 keys matching `^[a-z0-9_.-]{1,64}$`, values of 1 to 256 characters
(numbers and booleans become text); an invalid entry is dropped with a debug log.

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

Since 0.2.0, every batch of spans also carries a small `client` object with the
SDK name and version, a random `exporter_id` (new in each process, including a
forked child) and two running totals for that exporter: `dropped_total` (spans
and scores dropped because the queue was full) and `failed_total` (spans and
scores lost after retries or rejected by the server). The totals are cumulative,
so a batch delivered twice does not count a loss twice. It holds no span
content, and older Spanlight servers ignore it.

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
