/** A stored span for the waterfall helper tests: `startMs` and `durationMs` from a fixed base. */
import type { Span } from "@/lib/api";

const BASE = Date.parse("2026-10-07T12:00:00.000Z");

export function testSpan(
  spanId: string,
  parentSpanId: string | null,
  startMs: number,
  durationMs: number,
  overrides: Partial<Span> = {},
): Span {
  return {
    span_id: spanId,
    parent_span_id: parentSpanId,
    kind: "other",
    name: spanId,
    status: "ok",
    status_message: null,
    error_class: null,
    provider_error_type: null,
    provider_error_code: null,
    rate_limit_kind: null,
    started_at: new Date(BASE + startMs).toISOString(),
    ended_at: new Date(BASE + startMs + durationMs).toISOString(),
    duration_ms: durationMs,
    provider: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    cached_tokens: null,
    cache_write_tokens: null,
    cache_write_1h_tokens: null,
    service_tier: null,
    web_search_requests: null,
    cost_usd: null,
    fee_usd: null,
    pricing_version: null,
    time_to_first_token_ms: null,
    input: null,
    output: null,
    attributes: {},
    truncated: false,
    payload_omitted: false,
    finish_reason: null,
    prompt_version_id: null,
    usage_rollup: false,
    usage_state: "not_applicable",
    request_hash: null,
    ...overrides,
  };
}
