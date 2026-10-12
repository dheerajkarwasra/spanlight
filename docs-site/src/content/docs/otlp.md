---
title: OTLP ingestion
description: Send OpenTelemetry GenAI traces to Spanlight over OTLP/HTTP, in JSON or protobuf, and see how attributes map to Spanlight fields.
sidebar:
  order: 3
---

Spanlight accepts OTLP/HTTP traces, so any OpenTelemetry GenAI exporter can report to it without the Python SDK. The native JSON endpoint (`POST /v1/traces`) is described at the end of this page.

## Endpoint

| | |
| --- | --- |
| URL | `POST <host>/v1/otlp/traces`, for example `http://localhost:8080/v1/otlp/traces` |
| Authentication | `Authorization: Bearer spl_live_…`, a project API key with the `ingest:write` scope (the default scope of a new key) |
| Content types | `application/x-protobuf` or `application/json`. Anything else is `415`. |
| Compression | `Content-Encoding: gzip` or `deflate` |
| Limits | 5 MiB per request (after decompression) and 1000 spans per request, otherwise `413` |
| Rate limit | 50 requests per second per key, with a burst of 100. Over the limit is `429` with `Retry-After`. |

The response is `200` with `{}` (JSON) or an empty `ExportTraceServiceResponse` (protobuf). If some spans were rejected, the body carries `partialSuccess` with `rejectedSpans` and an `errorMessage` naming the first few. A body that is not valid OTLP is `400` with code `INVALID_OTLP`. An API key is not a personal access token: a token on this endpoint is `401`.

## Configure an exporter

Point the exporter at the full traces URL and pass the key as a header. With the standard OpenTelemetry environment variables:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:8080/v1/otlp/traces
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer%20spl_live_..."
```

Use the traces-specific endpoint variable. The general `OTEL_EXPORTER_OTLP_ENDPOINT` makes exporters append `/v1/traces` to it, which is Spanlight's native endpoint, not the OTLP one. The `%20` is the URL-encoded space that the OpenTelemetry specification requires in header values.

In Python with the OpenTelemetry SDK:

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider(
    resource=Resource.create(
        {"service.name": "support-bot", "deployment.environment.name": "production"}
    )
)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint="http://localhost:8080/v1/otlp/traces",
            headers={"Authorization": "Bearer spl_live_..."},
        )
    )
)
trace.set_tracer_provider(provider)
```

A request by hand, to check the key and the endpoint:

```bash
now=$(date +%s)
curl -sS http://localhost:8080/v1/otlp/traces \
  -H "Authorization: Bearer $SPANLIGHT_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- <<EOF
{
  "resourceSpans": [{
    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "curl-test"}}]},
    "scopeSpans": [{"spans": [{
      "traceId": "5b8efff798038103d269b633813fc60c",
      "spanId": "eee19b7ec3c1b174",
      "name": "chat",
      "kind": 3,
      "startTimeUnixNano": "${now}000000000",
      "endTimeUnixNano": "$((now + 1))000000000",
      "attributes": [
        {"key": "gen_ai.request.model", "value": {"stringValue": "claude-haiku-4-5"}},
        {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "120"}},
        {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "40"}}
      ]
    }]}]
  }]
}
EOF
```

A successful call prints `{}`. Spans that started more than 90 days ago are rejected, so use a current timestamp.

## How attributes map

OpenTelemetry GenAI semantic-convention attributes become Spanlight fields; other attributes are kept on the span.

| Spanlight field | From |
| --- | --- |
| `model` | `gen_ai.response.model`, else `gen_ai.request.model`. The response model is the snapshot that was billed. |
| `provider` | `gen_ai.provider.name`, else `gen_ai.system` |
| Input tokens | `gen_ai.usage.input_tokens`, else the legacy `gen_ai.usage.prompt_tokens`. Include cached tokens in this count. |
| Output tokens | `gen_ai.usage.output_tokens`, else `gen_ai.usage.completion_tokens` |
| Cached tokens | `gen_ai.usage.cache_read_input_tokens` |
| Input and output | `gen_ai.input.messages` and `gen_ai.output.messages`, else the legacy `gen_ai.prompt` and `gen_ai.completion`. JSON strings are parsed. |
| Span kind | `llm` if the span has any `gen_ai.*` attribute; `http` for client spans with `http.*` or `url.*` attributes; `retrieval` with `db.*` attributes; otherwise `other` |
| Trace name | the name of the root span (`service.name` if it has none) |
| Environment | resource attribute `deployment.environment.name`, else `deployment.environment` |
| Release | resource attribute `service.version` |
| User and session | `user.id` and `session.id`, on the span or the resource |
| Status | the OTLP status code: unset, ok or error, with its message |

Cost is computed on the server from the model and the token counts. A span with an unknown model, or without token counts, has an unknown cost, not zero. As with the SDK, secrets are redacted from payloads before they are stored, and a project can switch payload capture off.

Trace and span ids in JSON requests are hex strings; base64 is accepted too. Integers such as the nanosecond timestamps are sent as strings, as the OTLP/JSON encoding says.

## Native JSON

`POST /v1/traces` takes the format the Python SDK sends, and is the simplest way to report from a language without an SDK:

```json
{
  "spans": [
    {
      "trace_id": "5b8efff798038103d269b633813fc60c",
      "span_id": "eee19b7ec3c1b174",
      "name": "answer",
      "kind": "llm",
      "status": "ok",
      "start_time": "2026-10-09T10:00:00+00:00",
      "end_time": "2026-10-09T10:00:01.500+00:00",
      "provider": "anthropic",
      "model": "claude-haiku-4-5",
      "usage": {"input_tokens": 120, "output_tokens": 40, "cached_tokens": 0},
      "input": {"messages": [{"role": "user", "content": "Hi"}]},
      "output": "Hello!",
      "trace": {"name": "support-request", "user_id": "u_42", "session_id": "chat-123", "tags": ["beta"]}
    }
  ]
}
```

Timestamps are ISO-8601 with an offset, or integer Unix nanoseconds. The response lists what was stored:

```json
{"accepted": 1, "rejected": []}
```

Each span is validated on its own, so one bad span appears in `rejected` (with its index, span id and reason) without failing the batch. A span that is sent again with the same ids updates the stored one, so retrying a batch does not double-count by design. The limits, the key scope and the rate limit are the same as for OTLP. Validation rules, limits and the merge rules for trace fields are listed under [ingestion in the API conventions](/docs/api/conventions/#ingestion-post-v1traces).
