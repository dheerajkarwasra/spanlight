import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { parseMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CallsErrorsChartSkeleton } from "./calls-errors-card";
import { toChartPoints } from "./chart-data";
import { CompletenessCardSkeleton } from "./completeness-card";
import { KpiRibbon, KpiRibbonSkeleton } from "./kpi-ribbon";
import { ModelTableSkeleton } from "./model-table";
import { projectBudget } from "./needs-attention";
import { NeedsAttention } from "./needs-attention-card";
import { CallsSection, CompletenessSection, ModelsSection } from "./overview-sections";
import { SpendByModel } from "./spend-by-model";
import { TracesTabsCard } from "./traces-tabs-card";
import type { OverviewQueries } from "./use-overview-queries";

/*
 * Strata on a 12-column grid (spec §4): the KPI ribbon; "What changed" (Calls & errors 7 |
 * Spend by model 5); "To do" (Needs attention 6 | Traces 6); Models; completeness. The DOM order
 * is the phone order (ribbon, Needs attention, Traces, Calls & errors, Spend by model, Models,
 * completeness); from lg the CSS order puts "What changed" above "To do", stacked full width
 * until xl puts each stratum's cards side by side. Each wrapper stretches its card to the row.
 */
const GRID = "grid gap-3 sm:gap-4 lg:grid-cols-12";
const PLACE = {
  ribbon: "lg:order-1 lg:col-span-12",
  attention: "lg:order-4 lg:col-span-12 xl:col-span-6",
  traces: "lg:order-5 lg:col-span-12 xl:col-span-6",
  calls: "lg:order-2 lg:col-span-12 xl:col-span-7",
  spend: "lg:order-3 lg:col-span-12 xl:col-span-5",
  models: "lg:order-6 lg:col-span-12",
  completeness: "lg:order-7 lg:col-span-12",
} as const;

function Cell({ place, children }: { place: keyof typeof PLACE; children: ReactNode }) {
  return <div className={cn("min-w-0 *:h-full", PLACE[place])}>{children}</div>;
}

export function OverviewDashboard({ queries, now }: { queries: OverviewQueries; now: Date }) {
  const { overview, monthToDate, timeseries, health, budgets, range } = queries;
  if (!overview.data) {
    return null;
  }
  const points = timeseries.data ? toChartPoints(timeseries.data, range) : null;
  // undefined while the score loads, null when it failed.
  const score = health.isError ? null : health.data;

  return (
    <div className={GRID}>
      <Cell place="ribbon">
        <KpiRibbon
          className={cn("transition-opacity", overview.isPlaceholderData && "opacity-60")}
          metrics={overview.data}
          monthToDateUsd={parseMoney(monthToDate.data?.current.cost_usd)}
          points={points}
          health={score}
          budget={budgets.data ? projectBudget(budgets.data) : null}
          range={range}
          now={now}
        />
      </Cell>
      <Cell place="attention">
        <NeedsAttention queries={queries} now={now} />
      </Cell>
      <Cell place="traces">
        <TracesTabsCard now={now} />
      </Cell>
      <Cell place="calls">
        <CallsSection queries={queries} now={now} />
      </Cell>
      <Cell place="spend">
        <SpendByModel queries={queries} />
      </Cell>
      <Cell place="models">
        <ModelsSection queries={queries} />
      </Cell>
      <Cell place="completeness">
        <CompletenessSection queries={queries} />
      </Cell>
    </div>
  );
}

/** Placeholders in the shape of the dashboard, so nothing jumps when data arrives. */
export function OverviewSkeleton() {
  const block = (
    <Card aria-hidden className="h-full min-h-[260px] p-5">
      <Skeleton className="h-4 w-32" />
    </Card>
  );
  return (
    <div className={GRID}>
      <Cell place="ribbon">
        <KpiRibbonSkeleton />
      </Cell>
      <Cell place="attention">{block}</Cell>
      <Cell place="traces">{block}</Cell>
      <Cell place="calls">
        <CallsErrorsChartSkeleton />
      </Cell>
      <Cell place="spend">{block}</Cell>
      <Cell place="models">
        <ModelTableSkeleton />
      </Cell>
      <Cell place="completeness">
        <CompletenessCardSkeleton />
      </Cell>
    </div>
  );
}
