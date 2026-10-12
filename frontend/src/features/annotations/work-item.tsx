import { Link } from "@tanstack/react-router";
import { Link2 } from "lucide-react";
import type { ReactNode } from "react";

import { CostValue } from "@/components/cost-value";
import { ErrorState } from "@/components/error-state";
import { UnknownValue } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { PayloadView, useTraceQuery } from "@/features/traces";
import type { QueueItem, TraceDetail } from "@/lib/api";
import { formatDuration, formatInteger } from "@/lib/format";

import { reviewedSpan, reviewingText } from "./queue-format";

/**
 * The left card of work mode (Figma "Annotate — work mode", Trace): the trace's name and chips,
 * a link to the full trace, its numbers, then the reviewed span's input and output.
 */
export function WorkItem({ item }: { item: QueueItem }) {
  const trace = useTraceQuery(item.trace_id);
  return (
    <Card
      role="region"
      aria-label="Item to review"
      className="flex min-w-0 flex-1 flex-col gap-4 p-5 sm:p-6"
    >
      {trace.data !== undefined ? (
        <TraceContent trace={trace.data} item={item} />
      ) : trace.isPending ? (
        <div role="status" aria-label="Loading the trace" className="flex flex-col gap-4">
          <Skeleton className="h-8 w-2/3 rounded-full" />
          <Skeleton className="h-16 rounded-tile" />
          <Skeleton className="h-40 rounded-tile" />
        </div>
      ) : (
        <ErrorState
          compact
          error={trace.error}
          title="Couldn't load this trace"
          onRetry={() => {
            void trace.refetch();
          }}
        />
      )}
    </Card>
  );
}

function TraceContent({ trace, item }: { trace: TraceDetail; item: QueueItem }) {
  const { orgId, projectId } = useProjectParams();
  const span = reviewedSpan(trace.spans, item.span_id);
  const model = span?.model ?? trace.models[0] ?? null;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="text-2xl font-bold [overflow-wrap:anywhere] text-foreground">
          {trace.name ?? <span className="text-muted-foreground">Unnamed trace</span>}
        </h2>
        {trace.environment ? (
          <span className="rounded-full border border-border bg-surface-muted px-[9px] py-[3px] text-2xs font-medium text-muted-foreground">
            {trace.environment}
          </span>
        ) : null}
        {trace.release ? (
          <span className="rounded-full border border-border-strong bg-surface px-[9px] py-[3px] font-mono text-2xs font-medium text-muted-foreground">
            {trace.release}
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">Trace ID</span>
        <span className="min-w-0 truncate font-mono text-foreground">{trace.trace_id}</span>
        <Link
          to="/$orgId/$projectId/traces/$traceId"
          params={{ orgId, projectId, traceId: trace.trace_id }}
          search={item.span_id === null ? {} : { span: item.span_id }}
          target="_blank"
          className="ml-auto inline-flex items-center gap-1.5 rounded-sm text-label font-semibold text-accent hover:underline"
        >
          <Link2 aria-hidden className="size-3.5" />
          Open trace
          <span className="sr-only">(opens in a new tab)</span>
        </Link>
      </div>
      <dl className="flex flex-wrap gap-x-[18px] gap-y-2.5 rounded-tile bg-surface-muted px-4 py-3">
        <Metric label="Reviewing">{reviewingText(item)}</Metric>
        <Metric label="Duration">{formatDuration(trace.duration_ms)}</Metric>
        <Metric label="Tokens">
          {`${formatInteger(trace.input_tokens) ?? "0"} → ${formatInteger(trace.output_tokens) ?? "0"}`}
        </Metric>
        <Metric label="Cost">
          <CostValue cost={trace.cost_usd} hasUnpriced={trace.has_unpriced} />
        </Metric>
        <Metric label="Model" mono>
          {model ?? <UnknownValue reason="No model was recorded for this call" />}
        </Metric>
      </dl>
      {span === null ? (
        <p className="text-sm text-muted-foreground">
          This trace has no span to show. Open the trace for its details.
        </p>
      ) : (
        <>
          <PayloadView label="Input" value={span.input} spanTruncated={span.truncated} />
          <PayloadView label="Output" value={span.output} spanTruncated={span.truncated} />
        </>
      )}
    </>
  );
}

function Metric({ label, mono, children }: { label: string; mono?: boolean; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-2xs font-medium text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono text-label font-bold" : "text-label font-semibold"}>
        {children}
      </dd>
    </div>
  );
}
