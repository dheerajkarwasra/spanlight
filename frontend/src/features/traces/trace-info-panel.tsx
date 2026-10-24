import type { ReactNode } from "react";

import { CopyButton } from "@/components/copy-button";
import { CostValue, UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import type { TraceDetail } from "@/lib/api";
import { formatDuration, formatInteger, formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

import { MetadataBlock } from "./metadata-block";
import { failedSpanCount } from "./span-tree";
import { TraceAttributeChips } from "./trace-attribute-chips";
import { TraceRetryLine } from "./trace-retry-line";
import { TraceTokens } from "./trace-tokens";

/**
 * Figma "Trace info" (D1): the trace's ID, retries, start, spans, environment, attribute chips and
 * metadata, under the summary bar while its Trace info button is on. Below the `@3xl` container
 * width the bar has no room for duration, tokens and cost, so they show here.
 */
export function TraceInfoPanel({ id, trace }: { id: string; trace: TraceDetail }) {
  const failed = failedSpanCount(trace.spans);
  return (
    <Card id={id} role="region" aria-label="Trace info" className="flex flex-col gap-4 p-5">
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="shrink-0 text-xs font-medium text-muted-foreground">Trace ID</span>
        <code title={trace.trace_id} className="truncate text-label text-foreground">
          {trace.trace_id}
        </code>
        <CopyButton value={trace.trace_id} label="Copy trace ID" className="size-7 shrink-0" />
      </div>
      <TraceRetryLine trace={trace} />
      <dl className="grid grid-cols-2 gap-4 @xl:grid-cols-3 @3xl:grid-cols-[12.25rem_repeat(2,minmax(0,1fr))]">
        <Stat label="Started" wrap>
          <SummaryValue
            value={formatTimestamp(trace.started_at)}
            reason="The start time is invalid"
          />
        </Stat>
        <Stat label="Spans">
          <span className="tabular">{formatInteger(trace.span_count)}</span>
          {failed > 0 ? (
            <span className="text-xs font-medium text-danger-text tabular">
              {formatInteger(failed)} failed
            </span>
          ) : null}
        </Stat>
        <Stat label="Environment">
          <SummaryValue value={trace.environment} reason="The SDK didn't send an environment" />
        </Stat>
        <Stat label="Duration" narrowOnly>
          <SummaryValue
            value={formatDuration(trace.duration_ms)}
            reason="Duration isn't known yet"
          />
        </Stat>
        <Stat label="Tokens" narrowOnly wrap>
          <TraceTokens trace={trace} />
        </Stat>
        <Stat label="Cost" narrowOnly>
          <CostValue
            cost={trace.cost_usd}
            hasUnpriced={trace.has_unpriced}
            unknownReason={UNKNOWN_TOTAL_REASON}
          />
        </Stat>
      </dl>
      <TraceAttributeChips trace={trace} />
      <MetadataBlock metadata={trace.metadata} />
    </Card>
  );
}

/** A summary value, or the unknown placeholder. */
function SummaryValue({ value, reason }: { value: string | null; reason: string }) {
  if (value === null || value === "") {
    return <UnknownValue reason={reason} />;
  }
  return (
    <span title={value} className="tabular">
      {value}
    </span>
  );
}

interface StatProps {
  label: string;
  /** Let the value wrap instead of staying on one line (a date and time on a phone). */
  wrap?: boolean;
  /** Shown only while the summary bar is too narrow to carry it. */
  narrowOnly?: boolean;
  children: ReactNode;
}

function Stat({ label, wrap = false, narrowOnly = false, children }: StatProps) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", narrowOnly && "@3xl:hidden")}>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm font-medium text-foreground",
          wrap ? "break-words" : "truncate whitespace-nowrap",
        )}
      >
        {children}
      </dd>
    </div>
  );
}
