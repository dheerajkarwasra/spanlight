import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";

import { RelativeTime } from "@/components/relative-time";
import { ValueOrUnknown } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { useProjectParams } from "@/features/shell";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { LabRun } from "./lab-queries";

/** The columns of the wide layout, shared with the labels above the rows. */
export const RUN_GRID =
  "@[44rem]:grid-cols-[7rem_minmax(0,1.4fr)_minmax(0,1.2fr)_minmax(0,1.6fr)_6.5rem]";

const clockFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function formatClock(iso: string): string | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : clockFormat.format(date);
}

/**
 * Figma "Gateway/Lab run row": when, which call, which scenario fired, how it ended, and the link
 * to the trace. The traces API does not carry the key, the injected status or the cache state, so
 * the row shows what it does carry: the trace's name and environment, and its error message.
 */
export function LabRunRow({ run }: { run: LabRun }) {
  const { orgId, projectId } = useProjectParams();
  const { trace, scenario } = run;
  const failed = trace.error_count > 0;

  return (
    <li
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 rounded-tile bg-surface-muted p-3.5 @[44rem]:py-2.5 @[44rem]:pr-3 @[44rem]:pl-4",
        RUN_GRID,
      )}
    >
      <span className="flex flex-col leading-tight">
        <time dateTime={trace.started_at} className="font-mono text-sm text-foreground">
          <ValueOrUnknown
            value={formatClock(trace.started_at)}
            reason="The timestamp could not be read."
          />
        </time>
        <RelativeTime iso={trace.started_at} className="text-xs text-subtle-foreground" />
      </span>
      <span className="order-last col-span-2 flex min-w-0 items-center gap-2 @[44rem]:order-0 @[44rem]:col-span-1">
        <span
          title={trace.name ?? undefined}
          className="truncate text-sm font-semibold text-foreground"
        >
          {trace.name ?? "Unnamed call"}
        </span>
        {trace.environment ? (
          <Badge
            variant="neutral"
            size="sm"
            title={trace.environment}
            className="max-w-[50%] min-w-0 shrink"
          >
            <span className="truncate">{trace.environment}</span>
          </Badge>
        ) : null}
      </span>
      <code className="order-2 w-fit justify-self-end rounded-full border border-border bg-surface px-2 py-0.5 font-mono text-xs text-foreground @[44rem]:order-0 @[44rem]:justify-self-start">
        {scenario}
      </code>
      <span
        title={trace.error_message ?? undefined}
        className={cn(
          "order-last col-span-2 truncate text-sm @[44rem]:order-0 @[44rem]:col-span-1",
          failed ? "font-semibold text-danger-text" : "text-muted-foreground",
        )}
      >
        {failed ? (trace.error_message ?? "Failed") : "Completed"}
      </span>
      <Link
        to="/$orgId/$projectId/traces/$traceId"
        params={{ orgId, projectId, traceId: trace.trace_id }}
        aria-label={`View trace of ${trace.name ?? "this call"}, ${scenario}, ${formatTimestamp(trace.started_at) ?? "unknown time"}`}
        className="order-3 col-span-2 inline-flex items-center gap-1 justify-self-start rounded-sm text-sm font-semibold text-accent hover:underline @[44rem]:order-0 @[44rem]:col-span-1 @[44rem]:justify-self-end"
      >
        View trace
        <ArrowUpRight aria-hidden className="size-3.5" />
      </Link>
    </li>
  );
}
