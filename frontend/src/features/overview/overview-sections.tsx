import { cn } from "@/lib/utils";

import { CallsErrorsChartError, CallsErrorsChartSkeleton } from "./calls-errors-card";
import { CallsErrorsChart } from "./calls-errors-chart";
import { toChartPoints } from "./chart-data";
import {
  CompletenessCard,
  CompletenessCardError,
  CompletenessCardSkeleton,
} from "./completeness-card";
import { shortRangeLabel } from "./hero";
import { HealthTile, HealthTileError, HealthTileSkeleton } from "./health-tile";
import { overviewBands, usedProviders } from "./incident-overlay";
import { LatestTraces, LatestTracesError, LatestTracesSkeleton } from "./latest-traces";
import { ModelTable, ModelTableError, ModelTableSkeleton } from "./model-table";
import { RecentErrors, RecentErrorsError, RecentErrorsSkeleton } from "./recent-errors";
import type { OverviewQueries } from "./use-overview-queries";

interface SectionProps {
  queries: OverviewQueries;
  now: Date;
  className?: string;
}

/**
 * The incidents the overlay shades: null while either the incidents or the models (which name the
 * providers in use) are unknown, so the switch only appears once the bands are known.
 */
function incidentBands(queries: OverviewQueries) {
  const { incidents, models, range } = queries;
  if (!incidents.data || !models.data) {
    return undefined;
  }
  return overviewBands(incidents.data.items, usedProviders(models.data), {
    start: new Date(range.from),
    end: new Date(range.to),
  });
}

export function CallsSection({ queries, now, className }: SectionProps) {
  const { timeseries, bucket, range, incidents } = queries;
  if (timeseries.isPending) {
    return <CallsErrorsChartSkeleton className={className} />;
  }
  if (timeseries.isError) {
    return (
      <CallsErrorsChartError
        className={className}
        error={timeseries.error}
        onRetry={() => {
          void timeseries.refetch();
        }}
      />
    );
  }
  return (
    <CallsErrorsChart
      className={className}
      data={{
        points: toChartPoints(timeseries.data),
        bucket,
        rangeLabel: shortRangeLabel(range),
        now,
      }}
      bands={incidentBands(queries)}
      pollingOff={incidents.data?.polling === "disabled"}
      isRefreshing={timeseries.isPlaceholderData}
    />
  );
}

export function CompletenessSection({ queries, className }: Omit<SectionProps, "now">) {
  const { completeness, range } = queries;
  if (completeness.isPending) {
    return <CompletenessCardSkeleton className={className} />;
  }
  if (completeness.isError) {
    return (
      <CompletenessCardError
        className={className}
        error={completeness.error}
        onRetry={() => {
          void completeness.refetch();
        }}
      />
    );
  }
  return (
    <CompletenessCard
      className={cn(
        "transition-opacity",
        completeness.isPlaceholderData && "opacity-60",
        className,
      )}
      result={completeness.data}
      rangeLabel={shortRangeLabel(range)}
    />
  );
}

export function HealthSection({ queries, className }: Omit<SectionProps, "now">) {
  const { health, range } = queries;
  if (health.isPending) {
    return <HealthTileSkeleton className={className} />;
  }
  if (health.isError) {
    return (
      <HealthTileError
        className={className}
        error={health.error}
        onRetry={() => {
          void health.refetch();
        }}
      />
    );
  }
  return (
    <HealthTile
      className={cn("transition-opacity", health.isPlaceholderData && "opacity-60", className)}
      health={health.data}
      rangeLabel={shortRangeLabel(range)}
    />
  );
}

export function LatestTracesSection({ queries, now }: SectionProps) {
  const { latestTraces, isLive } = queries;
  if (latestTraces.isPending) {
    return <LatestTracesSkeleton />;
  }
  if (latestTraces.isError) {
    return (
      <LatestTracesError
        error={latestTraces.error}
        onRetry={() => {
          void latestTraces.refetch();
        }}
      />
    );
  }
  return (
    <LatestTraces
      traces={latestTraces.data.items}
      isLive={isLive}
      isRefreshing={latestTraces.isPlaceholderData}
      now={now}
    />
  );
}

export function RecentErrorsSection({ queries, now }: SectionProps) {
  const { recentErrors } = queries;
  if (recentErrors.isPending) {
    return <RecentErrorsSkeleton />;
  }
  if (recentErrors.isError) {
    return (
      <RecentErrorsError
        error={recentErrors.error}
        onRetry={() => {
          void recentErrors.refetch();
        }}
      />
    );
  }
  return (
    <RecentErrors
      traces={recentErrors.data.items}
      hasMore={recentErrors.data.next_cursor !== null}
      isRefreshing={recentErrors.isPlaceholderData}
      now={now}
    />
  );
}

export function ModelsSection({ queries }: { queries: OverviewQueries }) {
  const { models } = queries;
  if (models.isPending) {
    return <ModelTableSkeleton />;
  }
  if (models.isError) {
    return (
      <ModelTableError
        error={models.error}
        onRetry={() => {
          void models.refetch();
        }}
      />
    );
  }
  return <ModelTable models={models.data} isRefreshing={models.isPlaceholderData} />;
}
