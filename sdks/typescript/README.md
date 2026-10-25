# Spanlight TypeScript SDK

Tracing for Node.js LLM applications. Wrap your OpenAI or Anthropic client, trace your own
functions, and every request shows up in
[Spanlight](https://github.com/dheerajkarwasra/spanlight/blob/main/README.md) with latency,
tokens, cost, errors and the full prompt and completion.

- **Low overhead.** Tracing never waits on the network: spans are queued in memory and sent in
  batches in the background.
- **Tracing never breaks your app.** SDK failures are logged and swallowed. Errors from your code
  and from the providers are recorded and reach your code as the same object.
- **No runtime dependencies.** `openai` and `@anthropic-ai/sdk` are optional peer dependencies.
- **Typed.** Written in TypeScript with `strict` on; ships ESM, CommonJS and type declarations.

## Install

The package is published to npm by the release workflow when an `sdk-typescript-v*` tag is pushed. Once the first such tag is released, install it with:

```bash
npm install spanlight
```

Until then, build it from the repository:

```bash
git clone https://github.com/dheerajkarwasra/spanlight
cd spanlight/sdks/typescript
npm ci
npm pack
```

Then install the tarball in your app with `npm install /path/to/spanlight-0.1.0.tgz`.

Node.js 22 or newer is required. The wrappers are tested with `openai` 7.39 and
`@anthropic-ai/sdk` 0.135.

## Quickstart

Create a project in Spanlight, generate an API key under **Settings → API keys**, and export it:

```bash
export SPANLIGHT_API_KEY=spl_live_...
export SPANLIGHT_HOST=http://localhost:8000   # or your hosted instance
```

```ts
import OpenAI from "openai";
import { init, wrapOpenAI } from "spanlight";

init();
const openai = wrapOpenAI(new OpenAI());

const completion = await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "How do refunds work?" }],
});
```

That call is now one trace with one `llm` span. Long-running servers need nothing else. Scripts,
CLIs and serverless handlers should `await flush()` before they return. When the event loop
empties, the SDK also flushes on `beforeExit`, with a 5 second budget; `beforeExit` does not run
on `process.exit()` or a signal, so call `await shutdown()` before those.

## Trace your own code

```ts
import { init, observe, updateTrace, withSpan } from "spanlight";

init({ environment: "production", release: "2026.10.19" });

const searchDocs = observe(async (query: string) => ["Refunds take 5-7 business days."], {
  name: "search-docs",
  kind: "retrieval",
});

const handle = observe(
  async (question: string, userId: string) => {
    updateTrace({ userId, sessionId: "chat-123", tags: ["beta"] });
    const docs = await searchDocs(question);
    return withSpan("format-answer", (span) => {
      const answer = `From our docs: ${docs[0]}`;
      span.setOutput(answer);
      return answer;
    });
  },
  { name: "support-request" },
);

await handle("How long do refunds take?", "u_42");
```

This produces one trace with three nested spans: `support-request`, `search-docs` and
`format-answer`. The current span is kept in an `AsyncLocalStorage`, so awaited calls, timers and
promises started inside a span are its children without passing anything around.

## OpenAI

`wrapOpenAI(client)` instruments `chat.completions.create` and `responses.create` on the client it
is given, including `stream: true`. The SDK's `chat.completions.stream()`, `responses.stream()` and
`.parse()` helpers call `create`, so they are traced too. An `AzureOpenAI` client is recorded with
the provider `azure_openai`, which the server does not price at OpenAI's rates. If you point the
SDK at a compatible API, name the provider: `wrapOpenAI(client, { provider: "openrouter" })`.

Chat Completions streams report usage only when you ask for it:

```ts
const stream = await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Tell me a joke" }],
  stream: true,
  stream_options: { include_usage: true },
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta.content ?? "");
}
```

The span ends when the stream is exhausted, when you break out of the loop, or when it fails, and
records time to first token. When a stream carries no usage, the output token count is left
unknown (never guessed) and `usage.unreported_reason` says why: `include_usage_not_set`,
`no_final_usage` (a Responses stream without its terminal event), `client_disconnected` (you
stopped reading) or `stream_error`.

The `service_tier` OpenAI answers with is recorded as `standard`, `flex`, `priority` or `batch`;
completed `web_search_call` items of a Responses answer count as billed web searches; Chat
Completions with `web_search_options` get `usage.fee_unknown = "openai_chat_web_search"`; and
requests with `inference_geo` or `speed: "fast"` get `usage.pricing_modifier_unknown`, as the
Python SDK does.

## Anthropic

```ts
import Anthropic from "@anthropic-ai/sdk";
import { init, wrapAnthropic } from "spanlight";

init();
const anthropic = wrapAnthropic(new Anthropic());

const message = await anthropic.messages.create({
  model: "claude-haiku-4-5",
  max_tokens: 512,
  messages: [{ role: "user", content: "Summarize our refund policy." }],
});

// The streaming helper goes through messages.create, so it is traced too.
const final = await anthropic.messages
  .stream({
    model: "claude-haiku-4-5",
    max_tokens: 512,
    messages: [{ role: "user", content: "Hi" }],
  })
  .on("text", (text) => process.stdout.write(text))
  .finalMessage();
```

`wrapAnthropic(client)` instruments `messages.create`, plain and `stream: true`, and so
`messages.stream()`.

## What gets recorded

| Field                         | Source                                                                                                         |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `provider`                    | `openai`, `azure_openai` or `anthropic`                                                                        |
| `model`                       | The model from the response (for example `gpt-4o-mini-2024-07-18`), else the requested one                     |
| `usage.input_tokens`          | The whole prompt, **including** cached tokens. For Anthropic: input + cache reads + cache writes               |
| `usage.output_tokens`         | Completion tokens                                                                                              |
| `usage.cached_tokens`         | Prompt tokens read from the provider's prompt cache                                                            |
| `usage.cache_write_tokens`    | Prompt tokens written to the cache (OpenAI `cache_write_tokens`, Anthropic `cache_creation_input_tokens`)      |
| `usage.cache_write_1h_tokens` | The part of the writes made with Anthropic's 1-hour TTL                                                        |
| `usage.service_tier`          | The tier the provider billed at                                                                                |
| `time_to_first_token_ms`      | Streaming calls only                                                                                           |
| `input`                       | The request body (`model`, `messages`, `temperature`, ...). Request options such as headers are never recorded |
| `output`                      | The assistant message, including tool calls                                                                    |
| `status` / `status_message`   | `error` and the provider's message when the call fails                                                         |

A failed call also carries `error.type` (for example `RateLimitError`), `http.status_code` and
`provider.request_id` when the provider answered. The server redacts common secret patterns on
ingestion, and a project can drop payloads entirely with `capture_payloads = false`.

## API

### `init(options?)`

Configures the global client used by `observe`, `withSpan` and the wrappers. Calling it again
replaces the previous client after flushing it. Without `init()`, the first traced call creates a
client from the environment variables.

| Option              | Environment variable    | Default                 | Meaning                                                          |
| ------------------- | ----------------------- | ----------------------- | ---------------------------------------------------------------- |
| `apiKey`            | `SPANLIGHT_API_KEY`     | none                    | Project API key. Without one the SDK warns once and does nothing |
| `host`              | `SPANLIGHT_HOST`        | `http://localhost:8000` | Base URL of the Spanlight API                                    |
| `environment`       | `SPANLIGHT_ENVIRONMENT` | none                    | Attached to every trace                                          |
| `release`           | `SPANLIGHT_RELEASE`     | none                    | Your app version or git SHA                                      |
| `enabled`           | `SPANLIGHT_ENABLED`     | `true`                  | `false` (or `0`, `no`, `off`) turns tracing into a no-op         |
| `flushIntervalMs`   |                         | `2000`                  | Longest a span waits before it is sent                           |
| `batchSize`         |                         | `100`                   | Spans per request (1 to 1000)                                    |
| `maxQueue`          |                         | `10000`                 | Buffered spans; when full, the oldest are dropped                |
| `gzip`              |                         | `true`                  | Gzip request bodies                                              |
| `timeoutMs`         |                         | `10000`                 | HTTP timeout per request                                         |
| `maxRetries`        |                         | `5`                     | Retries for network errors, `429` and `5xx`                      |
| `shutdownTimeoutMs` |                         | `5000`                  | Time budget for the flush before the process exits               |
| `fetch`             |                         | global `fetch`          | The `fetch` used to reach Spanlight                              |

### `observe(fn, { name, kind, captureInput, captureOutput }?)`

Returns a function that traces each call of `fn`. The arguments become the span input (as an
array) and the return value, or the value a returned promise resolves to, the output. `name`
defaults to the function's name (`anonymous` for an arrow function passed inline) and `kind` to `chain`; `kind` is one of `llm`, `tool`,
`retrieval`, `chain`, `http` or `other`. A thrown error or a rejected promise marks the span failed
and is re-thrown unchanged.

### `withSpan(name, (span) => ..., { kind, input, attributes }?)`

Runs the callback inside a new span and returns what it returns; a returned promise ends the span
when it settles. `Span` methods: `setInput`, `setOutput`, `setAttribute`, `setAttributes`,
`setModel(model, provider?)`, `setUsage({ inputTokens, outputTokens, cachedTokens,
cacheWriteTokens, cacheWrite1hTokens, serviceTier, webSearchRequests })`, `setFinishReason`,
`markFirstToken` and `recordError`. `span.traceId` and `span.spanId` are W3C Trace Context
compatible ids.

### `updateTrace({ name, userId, sessionId, tags }?)`

Sets trace-level fields on the current trace from anywhere inside it. Tags accumulate across
calls.

### `getCurrentTraceId()`, `flush(timeoutMs?)`, `shutdown(timeoutMs?)`

`getCurrentTraceId()` returns the current trace id, to put in your logs. `flush()` resolves `true`
once every span ended so far has been delivered, or `false` when the timeout passes first.
`shutdown()` flushes and then stops the exporter.

## Delivery guarantees

A batch goes out when it reaches `batchSize` spans or after `flushIntervalMs`. Failed requests are
retried with jittered exponential backoff on network errors, `429` and `5xx`, and `Retry-After` is
honored. A `413` splits the batch in two. Other `4xx` responses are not retried. Failures and spans
the server rejects are logged with `console.warn` and `console.error`. If the queue fills up, the
oldest spans are dropped and counted. The background timer never keeps the process alive.

Every batch carries a small `client` object with the SDK name (`spanlight-js`) and version, a
random `exporter_id` and two running totals, `dropped_total` and `failed_total`, which Spanlight
shows under telemetry completeness. It holds no span content.

Set `SPANLIGHT_DEBUG=1` to see what the SDK is doing.

## Limitations

- The wrappers instrument the client instance you pass in. A call read only through
  `.asResponse()` is never parsed, so it is not recorded; `.withResponse()` is.
- A stream you never read to the end, never break out of and never abort keeps its span open, so
  the span is not sent.
- Node.js only: edge runtimes and browsers are not supported.
- Not in this SDK yet (the Python SDK has them): prompts, scores, trace metadata, batch results and
  the rate-limit headers on error spans.

## Development

```bash
cd sdks/typescript
npm ci
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
```

The wrapper tests run the real `openai` and `@anthropic-ai/sdk` packages with a `fetch` that
answers with recorded-shape JSON and SSE bodies, so no network access or API keys are needed.
