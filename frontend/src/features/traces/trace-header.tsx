import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

import { CopyButton } from "@/components/copy-button";
import { CostValue, UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import type { TraceDetail } from "@/lib/api";
import { formatDuration, formatInteger, formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

import { InsightBadges } from "./insight-badges";
import { recallTracesSearch } from "./last-traces-search";
import { MetadataBlock } from "./metadata-block";
import { failedSpanCount } from "./span-tree";
import { TraceStatusBadge } from "./status";
import { TraceActions } from "./trace-actions";
import { TraceAttributeChips } from "./trace-attribute-chips";
import { TraceRetryLine } from "./trace-retry-line";

export function BackToTracesLink() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/traces"
      params={{ orgId, projectId }}
      search={recallTracesSearch()}
      className="inline-flex w-fit items-center gap-1.5 rounded-sm text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft aria-hidden className="size-4" />
      Traces
    </Link>
  );
}

/**
 * Figma "Trace header": title and status, trace ID, the ink summary strip, attribute chips and the
 * metadata block.
 */
export function TraceHeader({ trace }: { trace: TraceDetail }) {
  return (
    <Card className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <h1
          title={trace.name ?? undefined}
          className={cn(
            "min-w-0 truncate text-h1 text-foreground",
            !trace.name && "text-muted-foreground",
          )}
        >
          {trace.name ?? "Unnamed trace"}
        </h1>
        <TraceStatusBadge errorCount={trace.error_count} />
        <InsightBadges traceId={trace.trace_id} />
        <TraceActions trace={trace} />
      </div>

      <div className="flex min-w-0 items-center gap-1.5">
        <span className="shrink-0 text-xs font-medium text-muted-foreground">Trace ID</span>
        <code title={trace.trace_id} className="truncate text-label text-foreground">
          {trace.trace_id}
        </code>
        <CopyButton value={trace.trace_id} label="Copy trace ID" className="size-7 shrink-0" />
      </div>

      <TraceRetryLine trace={trace} />
      <TraceSummaryStrip trace={trace} />
      <TraceAttributeChips trace={trace} />
      <MetadataBlock metadata={trace.metadata} />
    </Card>
  );
}

function TraceSummaryStrip({ trace }: { trace: TraceDetail }) {
  const failed = failedSpanCount(trace.spans);
  const totalTokens = trace.input_tokens + trace.output_tokens;
  return (
    <dl className="grid grid-cols-2 gap-y-4 rounded-tile bg-hero-card py-4.5 text-hero-card-foreground sm:grid-cols-3 lg:grid-cols-[12.25rem_repeat(5,minmax(0,1fr))] dark:border dark:border-border">
      {/* Two columns at 375 px leave no room for date and time on one line: it wraps, never cuts. */}
      <Stat label="Started" wrap>
        <ValueOnInk
          value={formatTimestamp(trace.started_at)}
          reason="The start time is invalid"
          wrap
        />
      </Stat>
      <Stat label="Duration">
        <ValueOnInk value={formatDuration(trace.duration_ms)} reason="Duration isn't known yet" />
      </Stat>
      <Stat label="Spans">
        <span className="tabular">{formatInteger(trace.span_count)}</span>
        {failed > 0 ? (
          <span className="text-xs font-medium text-rail-danger tabular">
            {formatInteger(failed)} failed
          </span>
        ) : null}
      </Stat>
      <Stat label="Tokens (in → out)">
        <span className="tabular">{formatInteger(totalTokens)}</span>
        <span className="text-xs font-medium text-rail-muted-foreground tabular">
          <span className="sr-only">: </span>
          {formatInteger(trace.input_tokens)}
          <span aria-hidden> → </span>
          <span className="sr-only"> in, </span>
          {formatInteger(trace.output_tokens)}
          <span className="sr-only"> out</span>
        </span>
      </Stat>
      <Stat label="Cost">
        <CostValue
          cost={trace.cost_usd}
          hasUnpriced={trace.has_unpriced}
          unknownReason={UNKNOWN_TOTAL_REASON}
          unknownTone="on-ink"
        />
      </Stat>
      <Stat label="Environment">
        <ValueOnInk value={trace.environment} reason="The SDK didn't send an environment" />
      </Stat>
    </dl>
  );
}

interface ValueOnInkProps {
  value: string | null;
  reason: string;
  /** Wrap onto a second line below the sm breakpoint instead of truncating. */
  wrap?: boolean;
}

/** A strip value, or the unknown placeholder in light-on-dark colours. */
function ValueOnInk({ value, reason, wrap = false }: ValueOnInkProps) {
  if (value === null || value === "") {
    return <UnknownValue reason={reason} tone="on-ink" />;
  }
  return (
    <span title={value} className={cn("tabular", wrap ? "sm:truncate" : "truncate")}>
      {value}
    </span>
  );
}

function Stat({
  label,
  wrap = false,
  children,
}: {
  label: string;
  /** Let the value wrap below the sm breakpoint. */
  wrap?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-5 lg:border-l lg:border-rail-tile lg:first:border-l-0">
      <dt className="text-xs font-medium text-rail-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "flex min-w-0 items-baseline gap-2 text-card",
          wrap ? "sm:whitespace-nowrap" : "whitespace-nowrap",
        )}
      >
        {children}
      </dd>
    </div>
  );
}

export function TraceHeaderSkeleton() {
  return (
    <Card aria-hidden className="flex flex-col gap-4 p-5 sm:p-6">
      <Skeleton className="h-9 w-64 max-w-full" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="grid grid-cols-2 gap-y-4 rounded-tile bg-hero-card py-4.5 sm:grid-cols-3 lg:grid-cols-6 dark:border dark:border-border">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex flex-col gap-2 px-5">
            <div className="h-3 w-14 rounded-md bg-rail-tile" />
            <div className="h-4 w-20 rounded-md bg-rail-tile-hover" />
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-7 w-28 rounded-full" />
        <Skeleton className="h-7 w-24 rounded-full" />
      </div>
    </Card>
  );
}
