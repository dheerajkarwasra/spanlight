import type { LinkProps } from "@tanstack/react-router";
import { CircleDashed } from "lucide-react";

import { LOWER_BOUND_REASON } from "@/components/cost-value";
import { KpiCard } from "@/components/kpi-card";
import type { SparklineProps } from "@/components/sparkline";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { UNKNOWN_DASH_CLASS } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell/project-context";
import type { Budget, Health, OverviewMetrics } from "@/lib/api";
import { formatCost, formatDuration, formatInteger } from "@/lib/format";
import { bucketFor, type ResolvedRange } from "@/lib/time-range";
import { cn } from "@/lib/utils";

import { BudgetBar } from "./budget-bar";
import { formatBucketRange, type ChartPoint } from "./chart-data";
import {
  formatDeltaMagnitude,
  previousPeriodLabel,
  ribbonKpis,
  type RibbonKpi,
  type RibbonKpiId,
} from "./kpis";
import { SLOWEST_SORT } from "./use-overview-queries";

const SPARKLINES: Partial<Record<RibbonKpiId, Pick<SparklineProps, "tone" | "name" | "format">>> = {
  calls: { tone: "calls", name: "Calls", format: formatInteger },
  p95: { tone: "latency", name: "p95", format: formatDuration },
  spend: { tone: "cost", name: "Spend", format: formatCost },
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
              value={
                card.id === "doctor" && health === null ? (
                  <UnavailableScore reason={card.unknownReason} />
                ) : card.value === null ? null : (
                  <Value card={card} />
                )
              }
              unknownReason={card.unknownReason}
              unit={card.id === "doctor" && health !== null ? "/100" : undefined}
              chip={card.approximate ? "approx" : undefined}
              denominator={
                card.id === "doctor" && health === null ? (
                  // Figma 817:2676 puts "Unavailable" under the dash on phones; 817:2317 puts it
                  // beside the dash and the reason under it from `sm` up.
                  <>
                    <span className="sm:hidden">{card.denominator}</span>
                    <span className="max-sm:hidden">{card.unknownReason}</span>
                  </>
                ) : card.denominator === null ? undefined : (
                  <Denominator card={card} />
                )
              }
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
              sparkline={sparklineOf(card, points, range, now)}
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

function sparklineOf(
  card: RibbonKpi,
  points: ChartPoint[] | null,
  range: ResolvedRange,
  now: Date,
): Omit<SparklineProps, "className"> | undefined {
  const spec = SPARKLINES[card.id];
  if (!card.trend || !points || !spec) {
    return undefined;
  }
  const bucket = bucketFor(range.durationMs);
  return {
    ...spec,
    data: points.map((point, index) => ({
      label: formatBucketRange(point.bucketStart, bucket, now, point.partial),
      value: card.trend?.[index] ?? null,
      partial: point.partial,
    })),
  };
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

/**
 * The Doctor's failed score: the unknown dash (its reason is read, since the whole card is a link)
 * and, from `sm` up, "Unavailable" beside it.
 */
function UnavailableScore({ reason }: { reason: string }) {
  return (
    <span title={reason}>
      <span aria-hidden className={UNKNOWN_DASH_CLASS}>
        —
      </span>
      <span className="sr-only">Unknown: {reason}</span>
      <span className="ml-3 align-middle text-sm font-medium text-muted-foreground max-sm:hidden">
        Unavailable
      </span>
    </span>
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
