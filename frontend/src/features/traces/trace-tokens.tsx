import { UnknownValue } from "@/components/unknown-value";
import type { TraceDetail } from "@/lib/api";
import { formatCompact, formatInteger } from "@/lib/format";

/**
 * Cached input tokens across the trace's calls, counted like the trace totals (a usage rollup
 * repeats its call's usage, so it is left out); null when no call reported any.
 */
function traceCachedTokens(spans: TraceDetail["spans"]): number | null {
  let total: number | null = null;
  for (const span of spans) {
    if (!span.usage_rollup && span.cached_tokens !== null) {
      total = (total ?? 0) + span.cached_tokens;
    }
  }
  return total;
}

/** The trace totals count a call without usage as 0, so a trace where no call reported any is unknown. */
function reportsUsage(spans: TraceDetail["spans"]): boolean {
  return spans.some(
    (span) => !span.usage_rollup && (span.input_tokens !== null || span.output_tokens !== null),
  );
}

/**
 * "5,104 → 727 · 1.2K cached": the trace's input and output tokens, and the cached share unless
 * `compact` (the phone summary line).
 */
export function TraceTokens({ trace, compact = false }: { trace: TraceDetail; compact?: boolean }) {
  if (!reportsUsage(trace.spans)) {
    return <UnknownValue reason="No call in this trace reported token usage" />;
  }
  const cached = compact ? null : traceCachedTokens(trace.spans);
  return (
    <span className="tabular">
      {formatInteger(trace.input_tokens)}
      <span aria-hidden> → </span>
      <span className="sr-only"> in, </span>
      {formatInteger(trace.output_tokens)}
      <span className="sr-only"> out</span>
      {cached ? (
        <span className="text-muted-foreground">
          <span aria-hidden> · </span>
          <span className="sr-only">, </span>
          {formatCompact(cached)} cached
        </span>
      ) : null}
    </span>
  );
}
