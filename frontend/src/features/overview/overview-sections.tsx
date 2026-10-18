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
import { DoctorCard, DoctorCardError, DoctorCardSkeleton } from "./doctor-card";
import { providerStatusLine } from "./doctor-format";
import { overviewBands, usedProviders } from "./incident-overlay";
import { loadable } from "./loadable";
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
        points: toChartPoints(timeseries.data, range),
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

/**
 * The providers' status line: pending while the models (the providers in use) or the incidents
 * load; null when either failed (the Models card reports that error).
 */
function providerStatus(queries: OverviewQueries) {
  const { incidents, models } = queries;
  if (incidents.isError || models.isError) {
    return null;
  }
  if (!incidents.data || !models.data) {
    return { status: "pending" } as const;
  }
  return {
    status: "success",
    data: providerStatusLine(incidents.data, usedProviders(models.data)),
  } as const;
}

export function DoctorSection({ queries, now, className }: SectionProps) {
  const { health, overview, detectorRuns, topInsight } = queries;
  if (health.isPending || !overview.data) {
    return <DoctorCardSkeleton className={className} />;
  }
  if (health.isError) {
    return (
      <DoctorCardError
        className={className}
        error={health.error}
        onRetry={() => {
          void health.refetch();
        }}
      />
    );
  }
  return (
    <DoctorCard
      className={cn("transition-opacity", health.isPlaceholderData && "opacity-60", className)}
      health={health.data}
      metrics={overview.data.current}
      runs={loadable(detectorRuns, (runs) => runs)}
      topInsight={topInsight.data}
      providers={providerStatus(queries)}
      now={now}
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
