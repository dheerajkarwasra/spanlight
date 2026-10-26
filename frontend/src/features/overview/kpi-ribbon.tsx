import type { LinkProps } from "@tanstack/react-router";
import { CircleDashed } from "lucide-react";

import { LOWER_BOUND_REASON } from "@/components/cost-value";
import { KpiCard } from "@/components/kpi-card";
import type { SparklineTone } from "@/components/sparkline";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import type { Budget, Health, OverviewMetrics } from "@/lib/api";
import type { ResolvedRange } from "@/lib/time-range";
import { cn } from "@/lib/utils";

import { BudgetBar } from "./budget-bar";
import type { ChartPoint } from "./chart-data";
import {
  formatDeltaMagnitude,
  previousPeriodLabel,
  ribbonKpis,
  type RibbonKpi,
  type RibbonKpiId,
} from "./kpis";
import { SLOWEST_SORT } from "./use-overview-queries";

const SPARKLINE_TONES: Partial<Record<RibbonKpiId, SparklineTone>> = {
  calls: "calls",
  p95: "latency",
};

interface KpiRibbonProps {
  metrics: OverviewMetrics;
  /** The UTC month's spend so far; null while unknown. */
  monthToDateUsd: number | null;
  /** The timeseries behind the sparklines; null while unknown. */
  points: ChartPoint[] | null;
  /** The Doctor's score: undefined while loading, null when it failed. */
  health: Health | null | undefined;
  /** The enabled project-wide budget, when there is one. */
  budget: Budget | null;
  range: ResolvedRange;
  now: Date;
  className?: string;
}

/** The "Now" stratum (spec §4 O1): four headline cards, each a link to where it is explained. */
export function KpiRibbon({
  metrics,
  monthToDateUsd,
  points,
  health,
  budget,
  range,
  now,
  className,
}: KpiRibbonProps) {
  const { orgId, projectId } = useProjectParams();
  const params = { orgId, projectId };
  const links: Record<RibbonKpiId, LinkProps> = {
    calls: { to: "/$orgId/$projectId/traces", params },
    p95: {
      to: "/$orgId/$projectId/traces",
      params,
      search: SLOWEST_SORT,
    },
    spend: { to: "/$orgId/$projectId/attribution", params },
    doctor: { to: "/$orgId/$projectId/doctor", params },
  };
  const cards = ribbonKpis({ metrics, monthToDateUsd, points, health: health ?? null, range, now });
  const comparisonLabel = `vs ${previousPeriodLabel(range)}`;

  return (
    <ul
      aria-label="Key metrics"
      className={cn("grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4", className)}
    >
      {cards.map((card) => (
        <li key={card.id} className="min-w-0">
          {card.id === "doctor" && health === undefined ? (
            <KpiSkeleton />
          ) : (
            <KpiCard
              label={card.label}
              value={card.value === null ? null : <Value card={card} />}
              unknownReason={card.unknownReason}
              unit={card.id === "doctor" ? "/100" : undefined}
              chip={card.approximate ? "approx" : undefined}
              denominator={card.denominator === null ? undefined : <Denominator card={card} />}
              delta={
                card.delta
                  ? {
                      delta: card.delta.value,
                      increaseIsGood: card.delta.increaseIsGood,
                      format: (absolute) =>
                        formatDeltaMagnitude(card.delta?.unit ?? "percent", absolute),
                      comparisonLabel,
                    }
                  : undefined
              }
              sparkline={sparklineOf(card)}
              href={links[card.id]}
            >
              {card.id === "spend" && budget ? <BudgetBar budget={budget} /> : null}
            </KpiCard>
          )}
        </li>
      ))}
    </ul>
  );
}

function sparklineOf(card: RibbonKpi) {
  const tone = SPARKLINE_TONES[card.id];
  return card.trend && tone ? { values: card.trend, tone } : undefined;
}

function KpiSkeleton() {
  return (
    <Card aria-hidden className="flex h-full min-h-[110px] flex-col gap-2 p-4">
      <Skeleton className="h-3 w-16" />
      <Skeleton className="h-7 w-20" />
      <Skeleton className="h-3 w-28" />
    </Card>
  );
}

export function KpiRibbonSkeleton() {
  return (
    <div aria-hidden className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {Array.from({ length: 4 }, (_, index) => (
        <KpiSkeleton key={index} />
      ))}
    </div>
  );
}

/** The line under the value; the Doctor's critical findings in danger text (spec §4). */
function Denominator({ card }: { card: RibbonKpi }) {
  if (card.critical === 0) {
    return card.denominator;
  }
  return (
    <>
      {card.denominator} · <span className="text-danger-text">{card.critical} critical</span>
    </>
  );
}

/**
 * The value; spend keeps its exact amount in the title and for screen readers, and gets the
 * lower-bound mark (not focusable: the whole card is a link).
 */
function Value({ card }: { card: RibbonKpi }) {
  if (card.exact === null) {
    return card.value;
  }
  const title = card.lowerBound ? `${card.exact}. ${LOWER_BOUND_REASON}` : card.exact;
  return (
    <span className="inline-flex items-center gap-1" title={title}>
      {card.value}
      {card.exact !== card.value ? <span className="sr-only">, exactly {card.exact}</span> : null}
      {card.lowerBound ? (
        <>
          <CircleDashed aria-hidden className="size-3.5 text-warning" />
          <span className="sr-only">, a lower bound</span>
        </>
      ) : null}
    </span>
  );
}
