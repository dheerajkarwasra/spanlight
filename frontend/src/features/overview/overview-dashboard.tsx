import { cn } from "@/lib/utils";

import { CallsErrorsChartSkeleton } from "./calls-errors-card";
import { toChartPoints } from "./chart-data";
import { CompletenessCardSkeleton } from "./completeness-card";
import { HealthTileSkeleton } from "./health-tile";
import { KpiCardsSkeleton } from "./kpi-card-placeholders";
import { KpiCards } from "./kpi-cards";
import { previousPeriodLabel } from "./kpis";
import { LatestTracesSkeleton } from "./latest-traces";
import { loadable, sparklinePoints } from "./loadable";
import { ModelTableSkeleton } from "./model-table";
import {
  CallsSection,
  CompletenessSection,
  HealthSection,
  LatestTracesSection,
  ModelsSection,
  RecentErrorsSection,
} from "./overview-sections";
import { RecentErrorsSkeleton } from "./recent-errors";
import { SpendCard, SpendCardSkeleton } from "./spend-card";
import type { OverviewQueries } from "./use-overview-queries";

/* Desktop bento: the spend card beside the health tile, calls chart, telemetry completeness and KPI
 * grid, then latest traces beside
 * recent errors, then models. Below xl everything stacks; on phones the KPI cards come before
 * the chart, with the health tile after them, as in the mobile design. */
const ROW_ONE = "grid gap-4 xl:grid-cols-[minmax(0,442fr)_minmax(0,626fr)]";
const ROW_TWO = "grid gap-4 xl:grid-cols-[minmax(0,717fr)_minmax(0,351fr)]";

export function OverviewDashboard({ queries, now }: { queries: OverviewQueries; now: Date }) {
  const { overview, timeseries, models, range, bucket } = queries;
  if (!overview.data) {
    return null;
  }
  const metrics = overview.data;
  const points = loadable(timeseries, toChartPoints);
  const dimMetrics = overview.isPlaceholderData ? "opacity-60" : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className={ROW_ONE}>
        <SpendCard
          className={cn("transition-opacity", dimMetrics)}
          metrics={metrics}
          range={range}
          bucket={bucket}
          points={points}
          models={loadable(models, (data) => data)}
          now={now}
        />
        <div className="flex min-w-0 flex-col gap-4">
          <HealthSection queries={queries} className="max-sm:order-3" />
          <CallsSection queries={queries} now={now} className="max-sm:order-2" />
          <CompletenessSection queries={queries} className="max-sm:order-2" />
          <KpiCards
            className={cn("transition-opacity max-sm:order-1", dimMetrics)}
            metrics={metrics}
            points={sparklinePoints(points)}
            comparisonLabel={`vs ${previousPeriodLabel(range)}`}
          />
        </div>
      </div>
      <div className={ROW_TWO}>
        <LatestTracesSection queries={queries} now={now} />
        <RecentErrorsSection queries={queries} now={now} />
      </div>
      <ModelsSection queries={queries} />
    </div>
  );
}

/** Placeholders in the shape of the dashboard, so nothing jumps when data arrives. */
export function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <div className={ROW_ONE}>
        <SpendCardSkeleton />
        <div className="flex min-w-0 flex-col gap-4">
          <HealthTileSkeleton className="max-sm:order-3" />
          <CallsErrorsChartSkeleton className="max-sm:order-2" />
          <CompletenessCardSkeleton className="max-sm:order-2" />
          <KpiCardsSkeleton className="max-sm:order-1" />
        </div>
      </div>
      <div className={ROW_TWO}>
        <LatestTracesSkeleton />
        <RecentErrorsSkeleton />
      </div>
      <ModelTableSkeleton />
    </div>
  );
}
