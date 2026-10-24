import { Link } from "@tanstack/react-router";
import { CircleCheck, CircleX } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { useProjectParams } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";

import {
  describeAge,
  failedSpansLabel,
  formatAge,
  primaryModelLabel,
  traceDisplayName,
} from "./trace-rows";

/** The latest failed traces with the first error message, newest first. */
export function RecentErrorsList({ traces, now }: { traces: TraceSummary[]; now: Date }) {
  if (traces.length === 0) {
    return (
      <EmptyState
        icon={CircleCheck}
        title="No errors in this period"
        description="Traces with a failed span show up here."
        className="py-8"
      />
    );
  }
  return (
    <ul className="flex flex-col">
      {traces.map((trace) => (
        <li key={trace.trace_id} className="border-b border-border last:border-b-0">
          <ErrorRow trace={trace} now={now} />
        </li>
      ))}
    </ul>
  );
}

function ErrorRow({ trace, now }: { trace: TraceSummary; now: Date }) {
  const { orgId, projectId } = useProjectParams();
  const model = primaryModelLabel(trace.models);
  const failedSpans = failedSpansLabel(trace.error_count);
  const meta = [model, failedSpans].filter(Boolean).join(" · ");
  const age = formatAge(trace.started_at, now);

  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: trace.trace_id }}
      className="group -mx-2 flex items-start gap-3 rounded-tile px-2 py-3.5 transition-colors duration-200 hover:bg-surface-hover focus-visible:outline-offset-0"
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-[11px] bg-danger-subtle">
        <CircleX aria-hidden className="size-4 text-danger" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex items-baseline justify-between gap-3">
          <span className="truncate text-sm font-semibold text-foreground">
            <span className="sr-only">Failed trace: </span>
            {traceDisplayName(trace)}
          </span>
          {age ? (
            <time
              dateTime={trace.started_at}
              title={formatTimestamp(trace.started_at) ?? undefined}
              className="shrink-0 text-xs font-medium text-subtle-foreground tabular"
            >
              <span aria-hidden>{age} ago</span>
              <span className="sr-only">{describeAge(trace.started_at, now)}</span>
            </time>
          ) : null}
        </span>
        {trace.error_message ? (
          <span
            title={trace.error_message}
            className="truncate font-mono text-label text-danger-text"
          >
            {trace.error_message}
          </span>
        ) : (
          <span className="truncate text-label text-muted-foreground italic">
            No error message recorded
          </span>
        )}
        {meta ? (
          <span className="truncate text-xs font-medium text-muted-foreground">{meta}</span>
        ) : null}
      </span>
    </Link>
  );
}
