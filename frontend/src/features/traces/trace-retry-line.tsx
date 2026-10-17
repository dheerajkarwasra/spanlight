import { Info, RefreshCw } from "lucide-react";

import { Tooltip } from "@/components/ui/tooltip";
import type { TraceSummary } from "@/lib/api";

import { RETRY_HEADER_EXPLANATION, formatRetries, retrySummary } from "./retry-format";

/**
 * Figma "Traces/Detail/Retry line": "3 retries · $0.0412 retry cost" under the trace ID, with an
 * info button that explains what counts, or why a value is "—". Not rendered for a trace without
 * retries (a known zero).
 */
export function TraceRetryLine({
  trace,
}: {
  trace: Pick<TraceSummary, "retry_calls" | "retry_cost_usd">;
}) {
  const summary = retrySummary(trace.retry_calls, trace.retry_cost_usd);
  if (summary === null) {
    return null;
  }
  const reason = formatRetries(trace.retry_calls, trace.retry_cost_usd).reason;
  const explanation = reason ?? RETRY_HEADER_EXPLANATION;

  return (
    <p className="flex min-w-0 items-center gap-1.5 text-sm text-foreground">
      <RefreshCw aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 tabular">{summary}</span>
      <Tooltip content={explanation}>
        <button
          type="button"
          aria-label={`About retries: ${explanation}`}
          className="relative inline-flex size-6 shrink-0 cursor-help items-center justify-center rounded-full text-muted-foreground transition-colors before:absolute before:-inset-2.5 before:content-[''] hover:text-foreground sm:before:hidden"
        >
          <Info aria-hidden className="size-4" />
        </button>
      </Tooltip>
    </p>
  );
}
