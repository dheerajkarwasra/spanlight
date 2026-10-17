import { Link } from "@tanstack/react-router";

import { UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatCost, formatDuration, formatRelativeTime, formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

import { MetadataChips } from "./metadata-chips";
import { RetryBadge } from "./retry-cell";
import { hasUnknownRetries, RETRY_TILES_FOOTNOTE } from "./retry-format";
import { TraceStatusDot } from "./status";
import { MonoBadge, TraceErrorLine } from "./trace-cells";

interface TraceTilesProps {
  traces: TraceSummary[];
  selectedTraceId?: string | null;
  stale?: boolean;
  /** The project's promoted metadata keys, shown as chips on each tile that carries them. */
  promotedKeys?: readonly string[];
}

/** Phone layout of the traces list (Figma "Traces/Mobile tile"): one tappable card per trace. */
export function TraceTiles({
  traces,
  selectedTraceId = null,
  stale = false,
  promotedKeys = [],
}: TraceTilesProps) {
  return (
    <div className="flex flex-col gap-3">
      <ul
        aria-label="Traces"
        aria-busy={stale || undefined}
        className={cn("flex flex-col gap-2 transition-opacity", stale && "opacity-60")}
      >
        {traces.map((trace) => (
          <li key={trace.trace_id}>
            <TraceTile
              trace={trace}
              selected={trace.trace_id === selectedTraceId}
              promotedKeys={promotedKeys}
            />
          </li>
        ))}
      </ul>
      {/* The badges sit inside links, so the "—" forms are explained here, not in tooltips. */}
      {hasUnknownRetries(traces) ? (
        <p className="text-xs font-medium text-muted-foreground">{RETRY_TILES_FOOTNOTE}</p>
      ) : null}
    </div>
  );
}

interface TraceTileProps {
  trace: TraceSummary;
  selected: boolean;
  promotedKeys: readonly string[];
}

function TraceTile({ trace, selected, promotedKeys }: TraceTileProps) {
  const { orgId, projectId } = useProjectParams();
  const hasName = trace.name !== null && trace.name !== "";
  const cost = formatCost(trace.cost_usd);
  const duration = formatDuration(trace.duration_ms);
  const started = formatRelativeTime(trace.started_at);
  const model = trace.models[0];
  const extraModels = trace.models.length - 1;

  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: trace.trace_id }}
      className={cn(
        "flex flex-col gap-2 rounded-tile border border-border px-4 py-3.5 transition-colors",
        selected ? "bg-surface-selected" : "bg-surface hover:bg-surface-hover",
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <TraceStatusDot static errorCount={trace.error_count} errorMessage={trace.error_message} />
        <span
          className={cn(
            "min-w-0 flex-1 truncate font-semibold text-foreground",
            !hasName && "font-medium text-muted-foreground italic",
          )}
        >
          {hasName ? trace.name : "Unnamed trace"}
        </span>
        <span className="shrink-0 font-medium text-foreground tabular">
          {cost ?? <UnknownInLink reason={UNKNOWN_TOTAL_REASON} />}
        </span>
      </span>
      <span className="flex min-w-0 items-center gap-2 pl-4 text-xs font-medium text-muted-foreground">
        {model ? <MonoBadge value={model} className="shrink" /> : <span>No LLM calls</span>}
        {extraModels > 0 ? <span className="shrink-0">+{extraModels}</span> : null}
        {duration ? <span className="shrink-0 tabular">{duration}</span> : null}
        {started ? (
          <>
            <span aria-hidden className="text-subtle-foreground">
              ·
            </span>
            <time
              dateTime={trace.started_at}
              title={formatTimestamp(trace.started_at) ?? undefined}
              className="shrink-0 tabular"
            >
              {started}
            </time>
          </>
        ) : null}
        <span className="ml-auto flex shrink-0">
          <RetryBadge trace={trace} />
        </span>
      </span>
      <TraceErrorLine
        errorClass={trace.error_class}
        message={trace.error_message}
        className="pl-4"
      />
      {/* Plain chips: the tile is already a link. */}
      <MetadataChips metadata={trace.metadata} promoted={promotedKeys} className="flex-wrap pl-4" />
    </Link>
  );
}

/** The "—" placeholder without a focusable tooltip, which a link can't contain. */
function UnknownInLink({ reason }: { reason: string }) {
  return (
    <span className="text-muted-foreground">
      <span aria-hidden>—</span>
      <span className="sr-only">Unknown: {reason}</span>
    </span>
  );
}

export function TraceTilesSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-hidden className="flex flex-col gap-2">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="flex flex-col gap-2.5 rounded-tile border border-border bg-surface px-4 py-3.5"
        >
          <div className="flex items-center gap-2">
            <Skeleton className="size-2 rounded-full" />
            <Skeleton className="h-4 w-32" />
            <Skeleton className="ml-auto h-4 w-16" />
          </div>
          <div className="flex items-center gap-2 pl-4">
            <Skeleton className="h-6 w-32 rounded-full" />
            <Skeleton className="h-3 w-24" />
          </div>
        </div>
      ))}
    </div>
  );
}
