import { getRouteApi } from "@tanstack/react-router";
import { useState, type KeyboardEvent } from "react";

import { SeriesKey } from "@/components/chart-parts";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import type { Bucket } from "@/lib/api";
import { SERIES_COLORS } from "@/lib/chart-theme";
import { formatCompact } from "@/lib/format";
import { cn } from "@/lib/utils";

import { bucketLabel, bucketWindow, markMinHeight, type HistogramBar } from "./histogram";
import type { TraceFilters } from "./search";
import { useTraceHistogramQuery } from "./trace-queries";

const tracesRoute = getRouteApi("/_authed/$orgId/$projectId/traces");
const SKELETON_BARS = [40, 65, 30, 80, 55, 70, 45, 90, 60, 35, 75, 50];
const ROVING_KEYS: Record<string, (index: number, last: number) => number> = {
  ArrowLeft: (index) => Math.max(0, index - 1),
  ArrowRight: (index, last) => Math.min(last, index + 1),
  Home: () => 0,
  End: (_index, last) => last,
};

/**
 * Spec T4: a 64 px strip of traces per bucket for the list's filters, failed traces stacked on top.
 * Each bar is a button (one tab stop, arrow keys move) that narrows the window to its bucket and
 * keeps the other filters. From `md` up, like the table: phone buckets would be a few px wide.
 */
export function TraceHistogram({ search }: { search: TraceFilters }) {
  const query = useTraceHistogramQuery(search);
  if (query.isPending) {
    return (
      <div aria-hidden className="hidden flex-col gap-1.5 md:flex">
        <Skeleton className="h-4 w-28" />
        <div className="flex h-16 items-end gap-0.5 pl-10">
          {SKELETON_BARS.map((height, index) => (
            <Skeleton
              key={index}
              className="flex-1 rounded-b-none"
              style={{ height: `${height}%` }}
            />
          ))}
        </div>
      </div>
    );
  }
  if (query.isError) {
    return <p className="hidden text-xs text-muted-foreground md:block">Histogram unavailable</p>;
  }
  if (query.data.bars.length === 0) {
    return null;
  }
  return (
    <HistogramStrip
      bars={query.data.bars}
      bucket={query.data.bucket}
      counted={query.data.window}
      stale={query.isPlaceholderData}
    />
  );
}

interface HistogramStripProps {
  bars: HistogramBar[];
  bucket: Bucket;
  /** The window the bars counted; a click never opens more than it. */
  counted: { from: string; to: string };
  stale: boolean;
}

function HistogramStrip({ bars, bucket, counted, stale }: HistogramStripProps) {
  const navigate = tracesRoute.useNavigate();
  const [focused, setFocused] = useState(bars.length - 1);
  const active = Math.min(focused, bars.length - 1);
  const peak = Math.max(1, ...bars.map((bar) => bar.traces));

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const move = ROVING_KEYS[event.key];
    if (!move) {
      return;
    }
    event.preventDefault();
    const next = move(active, bars.length - 1);
    setFocused(next);
    event.currentTarget.querySelectorAll("button")[next]?.focus();
  }

  return (
    <div className={cn("hidden flex-col gap-1.5 md:flex", stale && "opacity-60")}>
      <p className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <SeriesKey color={SERIES_COLORS.calls} shape="bar" />
          Traces
        </span>
        <span aria-hidden>·</span>
        <span className="flex items-center gap-1.5">
          <SeriesKey color={SERIES_COLORS.errors} shape="bar" />
          Errors
        </span>
      </p>
      <div className="flex h-16 gap-2">
        {/* The one y axis: the peak and the baseline. */}
        <div
          aria-hidden
          className="flex w-8 flex-col justify-between text-right text-xs leading-none text-muted-foreground tabular"
        >
          <span>{formatCompact(peak)}</span>
          <span>0</span>
        </div>
        <div
          role="group"
          aria-label="Traces over time"
          onKeyDown={onKeyDown}
          className="flex min-w-0 flex-1 border-b border-chart-grid"
        >
          {bars.map((bar, index) => {
            const label = bucketLabel(bar, bucket);
            return (
              <Tooltip key={bar.start} content={label}>
                <button
                  type="button"
                  aria-label={label}
                  tabIndex={index === active ? 0 : -1}
                  onFocus={() => {
                    setFocused(index);
                  }}
                  onClick={() => {
                    void navigate({
                      search: (prev) => ({
                        ...prev,
                        range: "custom",
                        ...bucketWindow(bar.start, bucket, counted),
                      }),
                    });
                  }}
                  className="group flex h-full min-w-0 flex-1 flex-col justify-end px-px focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <BarMark bar={bar} peak={peak} />
                </button>
              </Tooltip>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * At most 24 px wide, 4 px round top; failed traces on top, a 2 px gap between the two. Each
 * segment keeps 2 px, so a tiny bar never loses one under the gap.
 */
function BarMark({ bar, peak }: { bar: HistogramBar; peak: number }) {
  if (bar.traces === 0) {
    return null;
  }
  const ok = bar.traces - bar.errors;
  return (
    <span
      className="mx-auto flex w-full max-w-6 flex-col gap-0.5 overflow-hidden rounded-t-[4px] group-hover:opacity-80"
      style={{ height: `${(bar.traces / peak) * 100}%`, minHeight: markMinHeight(bar) }}
    >
      {bar.errors > 0 ? (
        <span className="min-h-0.5 bg-chart-2" style={{ flexGrow: bar.errors, flexBasis: 0 }} />
      ) : null}
      {ok > 0 ? (
        <span className="min-h-0.5 bg-chart-1" style={{ flexGrow: ok, flexBasis: 0 }} />
      ) : null}
    </span>
  );
}
