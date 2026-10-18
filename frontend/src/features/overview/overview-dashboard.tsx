import { cn } from "@/lib/utils";

import { CallsErrorsChartSkeleton } from "./calls-errors-card";
import { toChartPoints } from "./chart-data";
import { CompletenessCardSkeleton } from "./completeness-card";
import { DoctorCardSkeleton } from "./doctor-card";
import { KpiCardsSkeleton } from "./kpi-card-placeholders";
import { KpiCards } from "./kpi-cards";
import { previousPeriodLabel } from "./kpis";
import { LatestTracesSkeleton } from "./latest-traces";
import { loadable, sparklinePoints } from "./loadable";
import { ModelTableSkeleton } from "./model-table";
import {
  CallsSection,
  CompletenessSection,
  DoctorSection,
  LatestTracesSection,
  ModelsSection,
  RecentErrorsSection,
} from "./overview-sections";
import { RecentErrorsSkeleton } from "./recent-errors";
import { SpendCard, SpendCardSkeleton } from "./spend-card";
import type { OverviewQueries } from "./use-overview-queries";

/* Desktop bento: Spend | Calls & errors, then KPIs | Doctor (one grid, so both rows share the
 * 442 | 626 split and each row's cards end on one line), telemetry completeness, latest traces |
 * recent errors, then models. The DOM order is the phone order (Spend, KPIs, Calls, Doctor);
 * from xl the grid places each card in its column. */
const ROW_ONE = "grid gap-3 sm:gap-4 xl:grid-cols-[minmax(0,442fr)_minmax(0,626fr)]";
const ROW_FOUR = "grid gap-3 sm:gap-4 xl:grid-cols-[minmax(0,717fr)_minmax(0,351fr)]";
const LEFT_TOP = "xl:col-start-1 xl:row-start-1";
const LEFT_BOTTOM = "xl:col-start-1 xl:row-start-2";
const RIGHT_TOP = "xl:col-start-2 xl:row-start-1";
const RIGHT_BOTTOM = "xl:col-start-2 xl:row-start-2";

export function OverviewDashboard({ queries, now }: { queries: OverviewQueries; now: Date }) {
  const { overview, timeseries, models, range, bucket } = queries;
  if (!overview.data) {
    return null;
  }
  const metrics = overview.data;
  const points = loadable(timeseries, (data) => toChartPoints(data, range));
  const dimMetrics = overview.isPlaceholderData ? "opacity-60" : undefined;

  return (
    <div className="flex flex-col gap-3 sm:gap-4">
      <div className={ROW_ONE}>
        <SpendCard
          className={cn("transition-opacity", LEFT_TOP, dimMetrics)}
          metrics={metrics}
          range={range}
          bucket={bucket}
          points={points}
          models={loadable(models, (data) => data)}
          now={now}
        />
        <KpiCards
          className={cn("transition-opacity", LEFT_BOTTOM, dimMetrics)}
          metrics={metrics}
          points={sparklinePoints(points)}
          comparisonLabel={`vs ${previousPeriodLabel(range)}`}
        />
        <CallsSection queries={queries} now={now} className={RIGHT_TOP} />
        <DoctorSection queries={queries} now={now} className={RIGHT_BOTTOM} />
      </div>
      <CompletenessSection queries={queries} />
      <div className={ROW_FOUR}>
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
    <div className="flex flex-col gap-3 sm:gap-4">
      <div className={ROW_ONE}>
        <SpendCardSkeleton className={LEFT_TOP} />
        <KpiCardsSkeleton className={LEFT_BOTTOM} />
        <CallsErrorsChartSkeleton className={RIGHT_TOP} />
        <DoctorCardSkeleton className={RIGHT_BOTTOM} />
      </div>
      <CompletenessCardSkeleton />
      <div className={ROW_FOUR}>
        <LatestTracesSkeleton />
        <RecentErrorsSkeleton />
      </div>
      <ModelTableSkeleton />
    </div>
  );
}
