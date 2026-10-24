import { Info } from "lucide-react";
import type { ReactNode, Ref } from "react";

import { CostValue, UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import type { Span, TraceDetail } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CompareWithPopover } from "./compare-with-popover";
import { CopyLinkButton } from "./copy-link-button";
import { InsightBadges } from "./insight-badges";
import { ReplayButton } from "./replay-button";
import { TraceStatusIcon } from "./status";
import { TraceActions } from "./trace-actions";
import { TraceTokens } from "./trace-tokens";

/**
 * The bar is at least 56 px tall and sticks to the top of its scroll box; `className` sets the
 * offset. Below the `@3xl` container width Doctor findings wrap onto a second line, and the
 * actions wrap below the title rather than squeeze it under 10rem.
 */
const BAR =
  "sticky z-20 flex min-h-14 min-w-0 flex-wrap content-center items-center gap-x-3 gap-y-1.5 border-b border-border bg-background/95 py-2";

interface TraceSummaryBarProps {
  trace: TraceDetail;
  selectedSpan: Span | null;
  /** The Trace info panel below the bar: whether it is open, its element id and the toggle. */
  infoOpen: boolean;
  infoId: string;
  onInfoToggle: () => void;
  /** What Copy link copies; the current address when absent (the peek passes the full page's). */
  linkHref?: string;
  className?: string;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Figma "Trace detail / Summary bar" (D1, D13): status, name and Doctor findings; duration, tokens
 * and cost from the `@3xl` container width; then Trace info, Add to dataset, Add to queue, Compare
 * with…, Replay and Copy link.
 */
export function TraceSummaryBar({
  trace,
  selectedSpan,
  infoOpen,
  infoId,
  onInfoToggle,
  linkHref,
  className,
  ref,
}: TraceSummaryBarProps) {
  // Replay targets the selected span when it is a model call, else the trace's first one.
  const replaySpan =
    selectedSpan?.kind === "llm" ? selectedSpan : trace.spans.find((span) => span.kind === "llm");
  const duration = formatDuration(trace.duration_ms) ?? (
    <UnknownValue reason="Duration isn't known yet" />
  );
  const cost = (
    <CostValue
      cost={trace.cost_usd}
      hasUnpriced={trace.has_unpriced}
      unknownReason={UNKNOWN_TOTAL_REASON}
    />
  );
  return (
    <div ref={ref} className={cn(BAR, className)}>
      <div className="flex min-w-40 flex-[1_1_10rem] items-center gap-2">
        <TraceStatusIcon errorCount={trace.error_count} />
        <div className="min-w-0">
          <h1
            title={trace.name ?? undefined}
            className={cn(
              "truncate text-card text-foreground",
              !trace.name && "text-muted-foreground",
            )}
          >
            {trace.name ?? "Unnamed trace"}
          </h1>
          {/* Narrow (Figma 375): duration · tokens · cost on a second line under the name. */}
          <p className="truncate text-xs font-medium text-muted-foreground tabular @3xl:hidden">
            {duration} · <TraceTokens trace={trace} compact /> · {cost}
          </p>
        </div>
      </div>
      <div className="order-last flex min-w-0 basis-full empty:hidden @3xl:order-none @3xl:flex-1 @3xl:basis-auto">
        <InsightBadges traceId={trace.trace_id} compact />
      </div>
      <dl className="hidden shrink-0 items-baseline gap-4 @3xl:flex">
        <BarStat label="Duration">{duration}</BarStat>
        <BarStat label="Tokens">
          <TraceTokens trace={trace} />
        </BarStat>
        <BarStat label="Cost">{cost}</BarStat>
      </dl>
      <div className="flex shrink-0 items-center gap-1.5">
        <Tooltip content={infoOpen ? "Hide trace info" : "Trace info"}>
          <Button
            size="icon-sm"
            aria-label="Trace info"
            aria-expanded={infoOpen}
            aria-controls={infoOpen ? infoId : undefined}
            className={cn(infoOpen && "bg-surface-hover")}
            onClick={onInfoToggle}
          >
            <Info aria-hidden />
          </Button>
        </Tooltip>
        <TraceActions trace={trace} selectedSpan={selectedSpan} />
        <CompareWithPopover trace={trace} />
        {replaySpan ? <ReplayButton traceId={trace.trace_id} span={replaySpan} compact /> : null}
        <CopyLinkButton href={linkHref} />
      </div>
    </div>
  );
}

function BarStat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5 whitespace-nowrap">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium text-foreground tabular">{children}</dd>
    </div>
  );
}

export function TraceSummaryBarSkeleton() {
  return (
    <div aria-hidden className="flex h-14 items-center gap-3 border-b border-border">
      <Skeleton className="h-5 w-56 max-w-[50%]" />
      <div className="ml-auto flex gap-1.5">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="size-8 rounded-full" />
        ))}
      </div>
    </div>
  );
}
