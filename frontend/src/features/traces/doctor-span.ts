import type { Span } from "@/lib/api";

import { timeOf } from "./span-timeline";

/**
 * The span the Doctor reads for a trace (spec ruling 6), mirroring the backend's
 * `trace_excerpts` (`backend/app/insights/explain_queries.py`): the first failed span, LLM spans
 * first; else the first LLM span; else the first span. Ties go to the lower span id.
 */
export function doctorSpanId(spans: readonly Span[]): string | null {
  // One pass and one date parse per span: sorting a 5,000-span trace parsed dates ~100,000 times.
  const tier = (span: Span) => (span.status === "error" ? 0 : 2) + (span.kind === "llm" ? 0 : 1);
  let first: { span: Span; tier: number; start: number } | null = null;
  for (const span of spans) {
    const next = { span, tier: tier(span), start: timeOf(span.started_at) };
    const before =
      first === null ||
      (next.tier - first.tier ||
        next.start - first.start ||
        span.span_id.localeCompare(first.span.span_id)) < 0;
    if (before) {
      first = next;
    }
  }
  return first?.span.span_id ?? null;
}
