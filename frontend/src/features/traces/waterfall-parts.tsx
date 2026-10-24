import type { CSSProperties } from "react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Span } from "@/lib/api";
import { cn } from "@/lib/utils";

import { SpanKindIcon } from "./span-kind";
import { kindMeta } from "./span-kind-meta";
import { formatTickLabel, ttftOffsetPercent, type Tick } from "./span-timeline";
import type { Segment } from "./span-self-time";
import { kindsInTrace } from "./span-tree";
import { indentPx, ROW_GRID, TIMELINE_CELL } from "./waterfall-items";

/** The time axis over the timeline column; hidden with that column in a narrow tree pane. */
export function TimelineAxis({ ticks, sticky }: { ticks: Tick[]; sticky: boolean }) {
  return (
    <div
      aria-hidden
      className={cn(
        TIMELINE_CELL,
        "h-4 rounded-input bg-surface-muted pl-2",
        sticky && "sticky top-0 z-10",
      )}
    >
      <div className={cn(ROW_GRID, "h-full")}>
        <span className="truncate text-overline text-muted-foreground uppercase">Name</span>
        <div className="relative h-full text-xs font-medium text-subtle-foreground">
          {ticks.map((tick, index) => (
            <span
              key={tick.ms}
              style={{ left: `${tick.percent}%` }}
              className={cn(
                "absolute top-1/2 -translate-y-1/2 whitespace-nowrap tabular",
                index > 0 && tick.percent < 99.5 && "-translate-x-1/2",
                index > 0 && tick.percent >= 99.5 && "-translate-x-full",
              )}
            >
              {formatTickLabel(tick.ms)}
            </span>
          ))}
        </div>
        <span />
      </div>
    </div>
  );
}

/** A span's bar fill: child-covered time is the faint base, self time is solid on top (D9). */
export function BarFill({ fill, segments }: { fill: string; segments: readonly Segment[] }) {
  return (
    <span className="absolute inset-0 overflow-hidden rounded-full">
      <span className={cn("absolute inset-0 opacity-35", fill)} />
      {segments.map((segment) => (
        <span
          key={segment.start}
          style={{
            left: `${segment.start * 100}%`,
            width: `${(segment.end - segment.start) * 100}%`,
          }}
          className={cn("absolute inset-y-0", fill)}
        />
      ))}
    </span>
  );
}

/** Kind colours present in this trace, plus the TTFT marker when any span reports it. */
export function WaterfallLegend({ spans }: { spans: Span[] }) {
  const kinds = kindsInTrace(spans);
  const showsTtft = spans.some((span) => span.kind === "llm" && ttftOffsetPercent(span) !== null);
  return (
    <ul
      aria-label="Legend"
      className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-2 text-xs font-medium text-muted-foreground"
    >
      {kinds.map((kind) => (
        <li key={kind} className="flex items-center gap-1.5">
          <SpanKindIcon kind={kind} />
          {kindMeta(kind).label}
        </li>
      ))}
      {showsTtft ? (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-3.5 w-[3px] rounded-[2px] border border-rail bg-lime" />
          Time to first token
        </li>
      ) : null}
    </ul>
  );
}

export function SpanWaterfallSkeleton() {
  return (
    <Card aria-hidden className="@container flex flex-col gap-1 px-4 pt-2 pb-4">
      <div className="flex h-9 items-center">
        <Skeleton className="h-5 w-24" />
      </div>
      <div className="flex flex-col gap-0.5">
        <div className={cn(TIMELINE_CELL, "h-4 rounded-input bg-surface-muted")} />
        {[0, 1, 2, 1, 1, 1].map((depth, index) => (
          <div key={index} className={cn(ROW_GRID, "h-10")}>
            <Skeleton
              className="h-3.5"
              style={{ marginLeft: `${indentPx(depth)}px`, width: "60%" }}
            />
            <div className={cn(TIMELINE_CELL, "relative h-2.5")}>
              <Skeleton
                className="absolute inset-y-0 rounded-full"
                style={{ left: `${(index * 13) % 55}%`, width: `${25 + ((index * 17) % 40)}%` }}
              />
            </div>
            <Skeleton className="ml-auto h-3 w-10" />
          </div>
        ))}
      </div>
    </Card>
  );
}

/** A parent span the trace names but never stored (spec D11): context, not selectable. */
export function GhostRow({
  parentSpanId,
  depth,
  style,
  posInSet,
  setSize,
}: {
  parentSpanId: string;
  depth: number;
  style: CSSProperties | undefined;
  posInSet: number | undefined;
  setSize: number | undefined;
}) {
  return (
    <div
      role="treeitem"
      aria-disabled="true"
      aria-selected={false}
      aria-level={depth + 1}
      aria-posinset={posInSet}
      aria-setsize={setSize}
      title={parentSpanId}
      style={{ ...style, paddingLeft: `${indentPx(depth) + 18}px` }}
      className="flex h-10 items-center rounded-input border border-dashed border-border-strong text-sm text-muted-foreground"
    >
      <span className="truncate">Missing parent span · {parentSpanId.slice(0, 4)}…</span>
    </div>
  );
}
