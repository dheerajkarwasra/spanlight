---
title: Gateway quickstart
description: Point the OpenAI or Anthropic SDK at the Spanlight gateway with one base URL and get every call traced, priced and, if you choose, routed, retried and cached.
sidebar:
  order: 1
---

The gateway is an HTTP proxy for LLM providers. Your application keeps using the official OpenAI or Anthropic client; you change its `base_url` and its API key. Each call is then recorded as a trace in Spanlight with the prompt, completion, tokens, latency and cost (the span is written after the answer is sent; the [gateway runbook](/docs/runbooks/gateway/) names the metric that counts any that could not be), and the gateway can retry, fall back to another provider, cache answers and inject faults on the way.

Nothing is translated: an OpenAI-style request goes to an OpenAI-style provider (`openai` or `openai_compatible` credentials) and a Messages request goes to Anthropic. The response is forwarded as the provider sent it, streams included.

| Path | Surface | Errors come back as |
| --- | --- | --- |
| `POST /gw/v1/chat/completions` | OpenAI Chat Completions | OpenAI |
| `POST /gw/v1/responses` | OpenAI Responses | OpenAI |
| `POST /gw/v1/messages` | Anthropic Messages | Anthropic |
| `GET /gw/v1/models` | Model list | OpenAI, or Anthropic when the request carries `anthropic-version` |

## 1. Set up a credential, a route and a key

You do this once per project, in the dashboard under **Gateway**.

1. **Credentials.** An organization owner stores a provider API key (**Gateway, Credentials**). The key is encrypted at rest and never shown again. See [Provider credentials](/docs/gateway/credentials/).
2. **Routes.** An owner or admin creates a route that names the credential to use (**Gateway, Routes**). The first route of a project becomes its default. See [Routes, retries and fallbacks](/docs/gateway/routing/).
3. **Keys.** An owner or admin creates a gateway key (**Gateway, Keys**). It looks like `spl_gw_…`, is shown **once**, and belongs to one project. A key without a route of its own uses the project's default route.

A gateway key authenticates the gateway only. It is not a project API key (`spl_live_…`) and cannot call the dashboard API or send traces to `/v1/traces`.

## 2. Change the base URL

`<host>` is the address of your Spanlight web service, for example `http://localhost:8080` with the Compose stack. The gateway needs no CORS setup because it is meant for servers, not browsers; it sends no CORS headers, so a browser on another origin cannot call it. Keep the key out of front-end code.

### OpenAI SDK

The OpenAI SDK appends `/chat/completions` to its base URL, so the base URL ends in `/gw/v1`.

```python
from openai import OpenAI

client = OpenAI(
    base_url="<host>/gw/v1",
    api_key="spl_gw_…",  # the gateway key, not an OpenAI key
)

reply = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "Say hello."}],
)
print(reply.choices[0].message.content)
```

### Anthropic SDK

The Anthropic SDK appends `/v1/messages`, so the base URL ends in `/gw`.

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="<host>/gw",
    api_key="spl_gw_…",  # the gateway key, not an Anthropic key
)

message = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello."}],
)
print(message.content[0].text)
```

### curl

```bash
curl -sS "<host>/gw/v1/chat/completions" \
  -H "Authorization: Bearer spl_gw_…" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Say hello."}]}'
```

### How the key is sent

Send the key as `Authorization: Bearer spl_gw_…` or as `x-api-key: spl_gw_…` (the Anthropic SDK uses the second). If both headers are present and carry different values, the call is refused with `401`. The error body follows the path, not the header: a call to `/gw/v1/messages` always gets an Anthropic-shaped error.

## 3. See the trace

Open **Traces**. Each call is one `llm` span named after the surface and model, for example `chat_completions gpt-4.1-mini`. Streaming calls are recorded when the stream ends, or when the client goes away.

A gateway span carries the usual fields (provider, model, input and output, token usage, time to first token for streams, cost) and these attributes:

| Attribute | Meaning |
| --- | --- |
| `spanlight.gateway.key_id` | The gateway key that made the call |
| `spanlight.gateway.request_id` | The `X-Request-ID` of the call |
| `spanlight.gateway.surface` | `chat_completions`, `responses` or `messages` |
| `spanlight.gateway.route`, `spanlight.gateway.route_version` | The route and the saved version that served the call |
| `spanlight.gateway.target`, `spanlight.gateway.provider` | The credential name and provider that answered |
| `spanlight.gateway.attempts`, `spanlight.gateway.fallbacks` | How many upstream attempts were made and how many other targets were used |
| `spanlight.gateway.upstream_status` | The last provider status |
| `spanlight.gateway.overhead_ms` | Time spent in Spanlight rather than at the provider |
| `spanlight.cache` | `hit`, `miss`, `off` or `bypass` (see [Response cache](/docs/gateway/cache/)) |
| `spanlight.fault.scenario` | Set when a Lab fault was injected (see [Integration Lab](/docs/gateway/lab/)) |

Prompt and completion text is stored only while the project's `capture_payloads` setting is on, as for any other span. Token counts and cost are recorded either way. Cost comes from the same price table as the rest of Spanlight, including your [price overrides](/docs/api/conventions/); a model with no known price shows as unknown, never as zero.

For streaming OpenAI chat completions that do not set `stream_options`, the gateway adds `stream_options.include_usage: true` so the span has token counts, and keeps the extra usage-only chunk (empty `choices`) that OpenAI then sends out of your stream, so your code sees the stream it asked for. Nothing else in the request is changed apart from [model aliases](/docs/gateway/routing/#model-aliases).

## Join a trace you already started

Send these headers to attach the call to your own trace, session or user. All are optional.

| Header | Value | Bounds |
| --- | --- | --- |
| `x-spanlight-trace-id` | The id of an existing trace | 32 lowercase hex characters, not all zeros |
| `x-spanlight-parent-span-id` | The span the call is a child of | 16 lowercase hex characters, not all zeros; needs a trace id |
| `x-spanlight-session` | A session id | 1 to 256 characters |
| `x-spanlight-user` | An end-user id | 1 to 256 characters |
| `x-spanlight-tags` | Comma-separated tags | At most 20 tags of at most 64 characters; longer ones are dropped, repeats are ignored |

If either id is present but invalid, both are ignored and the call starts a new trace. A parent id without a trace id is ignored. A session or user id outside its bounds is ignored. The final tag list is the Lab tag (if a fault fired), the key's default tags, then the header tags, at most 20 in all.

With the Spanlight Python SDK, pass the ids of the active span:

```python
import spanlight

with spanlight.span("answer") as current:
    client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Say hello."}],
        extra_headers={
            "x-spanlight-trace-id": current.trace_id,
            "x-spanlight-parent-span-id": current.span_id,
            "x-spanlight-session": "session-42",
            "x-spanlight-tags": "checkout,beta",
        },
    )
```

The `x-spanlight-*` headers are read by the gateway and never sent to the provider. Neither are your gateway key, cookies or any header outside a short allowlist.

## Response headers

| Header | When | Value |
| --- | --- | --- |
| `X-Request-ID` | Always | The id of the call, also in error bodies and in the span |
| `X-Spanlight-Attempts` | Always | Upstream attempts made (`0` when the gateway answered without calling a provider) |
| `X-Spanlight-Cache` | Model calls with a key | `hit`, `miss`, `off` or `bypass` |
| `X-Spanlight-Code` | Errors | The stable [error code](/docs/gateway/errors/); `UPSTREAM_ERROR` for an error the provider returned |
| `X-Spanlight-Fault` | A Lab fault fired | The scenario name |
| `X-Upstream-Request-ID` | The provider sent one | The provider's own request id |

Rate-limit headers and `Retry-After` from the provider are forwarded.

## Limits

- **Request size.** At most 10 MB, counted after decompression. Bodies may be sent with `Content-Encoding: gzip` or `deflate`. The body must be a JSON object with a string `model`.
- **Per key.** A key can have a requests-per-minute limit and a tokens-per-minute limit. A call over either gets `429 RATE_LIMITED` with `Retry-After`.
- **Per organization.** The operator can cap all of an organization's gateway keys together with `GATEWAY_ORG_RPM_CEILING` (see [Configuration](/docs/configuration/)). It is checked first.
- **Models.** A key can list the models it may request (`allowed_models`, at most 100). A model outside the list gets `404 MODEL_NOT_ALLOWED`, checked before any alias is applied. `GET /gw/v1/models` then lists those models, or the route's alias names; with neither, the request goes to the first compatible provider and its answer is passed through.

## Next

- [Routes, retries and fallbacks](/docs/gateway/routing/)
- [Response cache](/docs/gateway/cache/)
- [Integration Lab](/docs/gateway/lab/)
- [Provider credentials](/docs/gateway/credentials/)
- [Error responses](/docs/gateway/errors/)
- [Run the gateway](/docs/runbooks/gateway/) as part of the api or as its own process
