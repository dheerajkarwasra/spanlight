import type { Span } from "@/lib/api";

import { timeOf } from "./span-timeline";

/**
 * The span the Doctor reads for a trace (spec ruling 6), mirroring the backend's
 * `trace_excerpts` (`backend/app/insights/explain_queries.py`): the first failed span, LLM spans
 * first; else the first LLM span; else the first span. Ties go to the lower span id.
 */
export function doctorSpanId(spans: readonly Span[]): string | null {
  const failedFirst = (span: Span) => (span.status === "error" ? 0 : 1);
  const llmFirst = (span: Span) => (span.kind === "llm" ? 0 : 1);
  const [first] = [...spans].sort(
    (a, b) =>
      failedFirst(a) - failedFirst(b) ||
      llmFirst(a) - llmFirst(b) ||
      timeOf(a.started_at) - timeOf(b.started_at) ||
      a.span_id.localeCompare(b.span_id),
  );
  return first?.span_id ?? null;
}
