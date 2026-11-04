/** A stored span for the waterfall helper tests: `startMs` and `durationMs` from a fixed base. */
import type { Span } from "@/lib/api";

import type { SpanNode } from "./span-tree";

const BASE = Date.parse("2026-10-07T12:00:00.000Z");

export function testSpan(
  spanId: string,
  parentSpanId: string | null,
  startMs: number,
  durationMs: number,
  overrides: Partial<Span> = {},
): Span {
  // The server's eligibility scalar for the scalar inputs; a cache hit or fault sets it itself.
  const retryEligible =
    (overrides.kind ?? "other") === "llm" &&
    !(overrides.usage_rollup ?? false) &&
    (overrides.request_hash ?? null) !== null;
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
    attributes_omitted: false,
    retry_eligible: retryEligible,
    finish_reason: null,
    prompt_version_id: null,
    usage_rollup: false,
    usage_state: "not_applicable",
    request_hash: null,
    ...overrides,
  };
}

/** A chain assembled without using the traversal under test. */
export function deepTestTree(count = 5_000) {
  const spans = Array.from({ length: count }, (_, i) =>
    testSpan(String(i), i === 0 ? null : String(i - 1), i, 1, {
      kind: "llm",
      input: "question",
      output: "answer",
    }),
  );
  const nodes = spans.map((span) => ({
    span,
    children: [] as SpanNode[],
    orphaned: false,
  }));
  for (let i = 1; i < nodes.length; i++) {
    const child = nodes[i];
    if (child) nodes[i - 1]?.children.push(child);
  }
  return { spans, roots: nodes.slice(0, 1) };
}

/**
 * `count` sibling spans (start times shuffled) whose `started_at` reads are counted, so a test can
 * tell a sort that parses each timestamp once from one that parses it on every comparison.
 */
export function countedStartReads(count: number) {
  let reads = 0;
  const spans = Array.from({ length: count }, (_, i) => {
    const span = testSpan(`s${String(i)}`, "root", (i * 7919) % count, 1, { kind: "llm" });
    const { started_at: startedAt } = span;
    return Object.defineProperty(span, "started_at", {
      get: () => {
        reads += 1;
        return startedAt;
      },
    });
  });
  return { spans, reads: () => reads };
}
