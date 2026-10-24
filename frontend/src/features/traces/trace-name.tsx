import { Link } from "@tanstack/react-router";

import type { TraceSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

import { TraceStatusDot } from "./status";

interface TraceNameProps {
  trace: TraceSummary;
  orgId: string;
  projectId: string;
  /** From 1280 px a plain click peeks; modified and middle clicks keep the link's full page. */
  onPeek?: (traceId: string) => void;
}

/** Status icon, the name as the row's link, and the first 8 characters of the trace ID. */
export function TraceName({ trace, orgId, projectId, onPeek }: TraceNameProps) {
  const hasName = trace.name !== null && trace.name !== "";
  return (
    <span className="flex min-w-0 items-center gap-2">
      <TraceStatusDot errorCount={trace.error_count} errorMessage={trace.error_message} />
      <Link
        to="/$orgId/$projectId/traces/$traceId"
        params={{ orgId, projectId, traceId: trace.trace_id }}
        data-trace-id={trace.trace_id}
        onClick={(event) => {
          const plain = !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);
          if (onPeek && plain && event.button === 0) {
            event.preventDefault();
            onPeek(trace.trace_id);
          }
        }}
        className={cn(
          "min-w-0 truncate rounded-sm font-semibold text-foreground hover:underline",
          !hasName && "font-medium text-muted-foreground italic",
        )}
      >
        {hasName ? trace.name : "Unnamed trace"}
      </Link>
      <span
        title={trace.trace_id}
        className="shrink-0 font-mono text-xs text-subtle-foreground group-hover/row:text-muted-foreground"
      >
        {trace.trace_id.slice(0, 8)}
      </span>
    </span>
  );
}
