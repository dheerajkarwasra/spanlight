import { X } from "lucide-react";
import { useId } from "react";

import { ChartViewToggle } from "@/components/chart-view-toggle";
import { ErrorState } from "@/components/error-state";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AttributionSeriesQuery } from "@/lib/api";
import { useChartView } from "@/lib/use-stored-choice";
import { cn } from "@/lib/utils";

import { useAttributionSeriesQuery } from "./attribution-queries";
import { PLOT_CLASS, SpendPlot } from "./spend-plot";
import { seriesBars } from "./spend-series";
import { SpendTable } from "./spend-table";

/** Per viewer, per browser: the card opens the way it was left. */
const VIEW_KEY = "spanlight-spend-chart-view";

interface SpendChartProps {
  /** The list's filters; the chart adds `value` for the focused group. */
  query: Omit<AttributionSeriesQuery, "value">;
  focus: string | undefined;
  onClearFocus: () => void;
  /** The list's `pending_traces`: today's traces the rollup hasn't counted yet. */
  pendingTraces: number | null;
}

/**
 * "Spend over time" (spec ruling 10, §3.3): the window's spend per bucket, or one group's. The
 * bars don't link, so they take no focus: pointers get the tooltip, keyboards the table view.
 */
export function SpendChart({ query, focus, onClearFocus, pendingTraces }: SpendChartProps) {
  const titleId = useId();
  const [view, setView] = useChartView(VIEW_KEY);
  const series = useAttributionSeriesQuery({ ...query, value: focus });
  const per = series.data?.bucket === "hour" ? "per hour" : "per UTC day";

  function body() {
    if (series.isPending) {
      return <Skeleton aria-hidden className={PLOT_CLASS} />;
    }
    if (series.isError) {
      return (
        <ErrorState
          compact
          error={series.error}
          title="Couldn't load spend over time"
          onRetry={() => {
            void series.refetch();
          }}
        />
      );
    }
    const bars = seriesBars(series.data);
    return (
      // The previous group's bars stay, dimmed, until the focused group's arrive.
      <div
        data-stale={series.isPlaceholderData || undefined}
        className={cn("transition-opacity", series.isPlaceholderData && "opacity-60")}
      >
        {view === "table" ? (
          <SpendTable bars={bars} per={per} />
        ) : (
          <SpendPlot bars={bars} per={per} pendingTraces={pendingTraces} />
        )}
      </div>
    );
  }

  return (
    <Card
      role="region"
      aria-labelledby={titleId}
      aria-busy={series.isFetching || undefined}
      className="flex flex-col gap-4 p-5 sm:p-6"
    >
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="flex min-w-0 basis-full flex-col gap-1 sm:flex-1 sm:basis-auto">
          <CardTitle id={titleId}>Spend over time</CardTitle>
          <p className="truncate text-xs font-medium text-muted-foreground">
            {focus ?? "All groups"} · {per}
          </p>
        </div>
        {focus === undefined ? null : (
          <p className="flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-surface-muted py-0.5 pr-1 pl-3 text-xs font-medium">
            <span className="min-w-0 truncate">
              Showing <code className="font-mono">{focus}</code>
            </span>
            <button
              type="button"
              onClick={onClearFocus}
              className="inline-flex min-h-6 shrink-0 items-center gap-1 rounded-full px-2 text-accent hover:underline max-md:min-h-11"
            >
              All groups
              <X aria-hidden className="size-3" />
            </button>
          </p>
        )}
        <ChartViewToggle view={view} onChange={setView} />
      </div>
      {body()}
    </Card>
  );
}
