import { useId, useState } from "react";

import { ChartDataTableView } from "@/components/chart-data-table";
import { SeriesKey } from "@/components/chart-parts";
import { ChartViewToggle } from "@/components/chart-view-toggle";
import { ErrorState } from "@/components/error-state";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Bucket, ErrorClass } from "@/lib/api";
import { errorClassColor, errorClassLabel } from "@/lib/error-class";
import { formatInteger } from "@/lib/format";
import { providerLabel } from "@/lib/provider-label";
import { useChartView, type ChartView } from "@/lib/use-stored-choice";
import { cn } from "@/lib/utils";

import { bucketRange } from "./bucket-labels";
import { ErrorClassPlot } from "./error-class-plot";
import { ErrorClassTable } from "./error-class-table";
import { presentClasses, toStackedPoints, type StackedPoint } from "./error-series";
import { FilterPill, type FilterOption } from "./filter-pill";
import { useErrorSeries } from "./providers-queries";

/** Per viewer, per browser: the card opens the way it was left. */
const ERRORS_VIEW_KEY = "spanlight-provider-errors-view";

interface ErrorsByClassChartProps {
  /** The providers seen in the window, for the provider filter. */
  providers: readonly (string | null)[];
  bucket: Bucket;
}

/**
 * Figma "Errors by class card": failed calls per bucket stacked by class, with a legend that
 * carries each class's total, a provider filter and a "Show table" view (required: four of the
 * class colours sit under 3:1 contrast on white, so the numbers must be readable without them).
 */
export function ErrorsByClassChart({ providers, bucket }: ErrorsByClassChartProps) {
  const titleId = useId();
  const [provider, setProvider] = useState<string | undefined>();
  const [view, setView] = useChartView(ERRORS_VIEW_KEY);
  const options: FilterOption<string>[] = providers
    .filter((slug): slug is string => slug !== null)
    .map((slug) => ({ value: slug, label: providerLabel(slug) }));
  // A new window without the chosen provider goes back to All (adjusting state during render).
  if (provider !== undefined && !options.some((option) => option.value === provider)) {
    setProvider(undefined);
  }
  const active = options.some((option) => option.value === provider) ? provider : undefined;
  const series = useErrorSeries(active);
  const scope = active === undefined ? "all providers" : providerLabel(active);

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <CardTitle id={titleId}>Errors by class</CardTitle>
          <p className="text-xs font-medium text-muted-foreground">
            Failed LLM calls per {bucket}
            <span className="max-sm:hidden">, stacked by class</span> · {scope} · UTC
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <FilterPill label="Provider" value={active} options={options} onChange={setProvider} />
          <ChartViewToggle view={view} onChange={setView} />
        </div>
      </div>
      <div className={cn("transition-opacity", series.isPlaceholderData && "opacity-60")}>
        {series.isPending ? (
          <Skeleton className="h-[260px] rounded-tile" />
        ) : series.isError ? (
          <ErrorState
            compact
            error={series.error}
            onRetry={() => void series.refetch()}
            title="Couldn't load errors by class"
          />
        ) : (
          <ChartBody points={toStackedPoints(series.data)} bucket={bucket} view={view} />
        )}
      </div>
    </Card>
  );
}

interface ChartBodyProps {
  points: StackedPoint[];
  bucket: Bucket;
  view: ChartView;
}

function ChartBody({ points, bucket, view }: ChartBodyProps) {
  const classes = presentClasses(points);
  if (classes.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center rounded-tile border border-dashed border-border px-6 text-center text-sm text-muted-foreground">
        No failed LLM calls in this window
      </div>
    );
  }
  const totals = classTotals(points, classes);
  const summary = `Failed LLM calls per ${bucket} by class: ${classes
    .map((name) => `${errorClassLabel(name)} ${formatInteger(totals[name]) ?? "0"}`)
    .join(", ")}`;

  return (
    <div className="flex flex-col gap-4">
      <ul aria-label="Error classes" className="flex flex-wrap gap-x-4 gap-y-2">
        {classes.map((name) => (
          <li key={name} className="flex items-center gap-1.5 text-xs font-medium">
            <SeriesKey color={errorClassColor(name)} shape="bar" />
            <span className="text-foreground">{errorClassLabel(name)}</span>
            <span className="text-muted-foreground tabular">{formatInteger(totals[name])}</span>
          </li>
        ))}
      </ul>
      {view === "table" ? (
        <ErrorClassTable points={points} classes={classes} bucket={bucket} />
      ) : (
        <>
          <ErrorClassPlot points={points} classes={classes} bucket={bucket} summary={summary} />
          <ChartDataTableView
            table={{
              caption: `Failed LLM calls per ${bucket} and class`,
              columns: [
                bucket === "hour" ? "Hour (UTC)" : "Day (UTC)",
                ...classes.map(errorClassLabel),
              ],
              rows: points.map((point) => ({
                key: point.bucketStart,
                cells: [
                  bucketRange(point.bucketStart, bucket),
                  ...classes.map((name) => formatInteger(point.counts[name] ?? 0) ?? "0"),
                ],
              })),
            }}
          />
        </>
      )}
      <p className="text-xs font-medium text-subtle-foreground">
        <span className="max-sm:hidden">
          Classes keep this order and colour on every chart. The table view lists the same counts
          per {bucket}.
        </span>
        <span className="sm:hidden">
          Tap a bar for its counts. The table view lists every {bucket}.
        </span>
      </p>
    </div>
  );
}

function classTotals(points: StackedPoint[], classes: ErrorClass[]): Record<string, number> {
  const totals: Record<string, number> = {};
  for (const name of classes) {
    totals[name] = points.reduce((sum, point) => sum + (point.counts[name] ?? 0), 0);
  }
  return totals;
}
