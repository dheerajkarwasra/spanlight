import { ApproxValue } from "@/components/approx-value";
import { DeltaPill } from "@/components/delta-pill";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { OverviewMetrics } from "@/lib/api";
import { fittedMetricFontSize } from "@/lib/metric-fit";
import { cn } from "@/lib/utils";

import type { ChartPoint } from "./chart-data";
import { CARD_LAYOUT } from "./kpi-card-placeholders";
import {
  buildKpiCards,
  formatDeltaMagnitude,
  KPI_ZERO_BASED,
  kpiTrend,
  type KpiCardModel,
  type KpiDelta,
  type KpiId,
} from "./kpis";
import { Sparkline, type SparklineTone } from "./sparkline";

const SPARKLINE_TONES: Record<KpiId, SparklineTone> = {
  p95: "latency",
  error_rate: "errors",
  llm_calls: "calls",
  tokens: "on-accent",
};

/** The Tokens card is the page's one accent card (violet → fuchsia; neutral raised in dark). */
const ACCENT_KPI: KpiId = "tokens";

interface KpiCardsProps {
  metrics: OverviewMetrics;
  /** Timeseries behind the sparklines: null while loading, "error" when it failed. */
  points: ChartPoint[] | null | "error";
  /** e.g. "vs previous 24h". */
  comparisonLabel: string;
  className?: string;
}

/**
 * p95 latency, error rate, LLM calls and tokens, each with a delta and a sparkline over the
 * whole window, stacked top to bottom so nothing truncates at 213 px. The values
 * share one size, fitted to the widest of them, so none is ever cut short ("50. …").
 */
export function KpiCards({ metrics, points, comparisonLabel, className }: KpiCardsProps) {
  const cards = buildKpiCards(metrics);
  const valueFontSize = fittedMetricFontSize(
    cards.map((card) =>
      card.value === null ? null : `${card.approximate ? "≈ " : ""}${card.value}`,
    ),
  );
  return (
    <ul aria-label="Key metrics" className={cn("grid grid-cols-2 gap-3 sm:gap-4", className)}>
      {cards.map((card) => (
        <li key={card.id} className="min-w-0">
          <KpiCard
            card={card}
            points={points}
            comparisonLabel={comparisonLabel}
            valueFontSize={valueFontSize}
          />
        </li>
      ))}
    </ul>
  );
}

interface KpiCardProps {
  card: KpiCardModel;
  points: KpiCardsProps["points"];
  comparisonLabel: string;
  valueFontSize: string | undefined;
}

function KpiCard({ card, points, comparisonLabel, valueFontSize }: KpiCardProps) {
  const accent = card.id === ACCENT_KPI;

  return (
    <Card
      variant={accent ? "accent" : "default"}
      className={cn(
        CARD_LAYOUT,
        accent &&
          "shadow-[0_14px_32px_-14px_color-mix(in_srgb,var(--accent-card-from)_60%,transparent)] dark:shadow-none",
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <p className={cn("truncate text-sm font-medium", accent ? "" : "text-muted-foreground")}>
          {card.label}
        </p>
        <KpiDeltaPill delta={card.delta} accent={accent} comparisonLabel={comparisonLabel} />
      </div>
      {/* The size container the fitted font size is measured against. */}
      <div className="@container">
        <p
          data-kpi-value
          style={valueFontSize ? { fontSize: valueFontSize } : undefined}
          className={cn("text-metric whitespace-nowrap", accent ? "" : "text-foreground")}
        >
          <ApproxValue
            value={card.value}
            approximate={card.approximate}
            unknownReason={card.unknownReason}
          />
        </p>
      </div>
      {card.detail ? (
        <p
          className={cn(
            "truncate text-xs font-medium tabular",
            accent ? "" : "text-subtle-foreground",
          )}
        >
          <KpiDetail card={card} />
        </p>
      ) : null}
      <div className="mt-auto pt-1.5">
        <KpiSparkline id={card.id} points={points} />
      </div>
    </Card>
  );
}

/** The line under the value. The p95 card's p50 may be an estimate, so it carries the "≈". */
function KpiDetail({ card }: { card: KpiCardModel }) {
  if (card.p50 === null) {
    return card.detail;
  }
  return (
    <>
      p50{" "}
      <ApproxValue
        value={card.p50}
        approximate={card.approximate}
        unknownReason={card.unknownReason}
      />
    </>
  );
}

function KpiSparkline({ id, points }: { id: KpiId; points: KpiCardsProps["points"] }) {
  if (points === null) {
    // The light shimmer would glare on the gradient card; use a soft translucent block there.
    return id === ACCENT_KPI ? (
      <span aria-hidden className="block h-8 w-full rounded-md bg-accent-card-foreground/15" />
    ) : (
      <Skeleton className="h-8 w-full" />
    );
  }
  if (points === "error") {
    // The calls chart shows the error and the retry; keep the card's shape here.
    return <span aria-hidden className="block h-8" />;
  }
  return (
    <Sparkline
      values={kpiTrend(id, points)}
      tone={SPARKLINE_TONES[id]}
      zeroBased={KPI_ZERO_BASED[id]}
    />
  );
}

interface KpiDeltaPillProps {
  delta: KpiDelta | null;
  accent: boolean;
  comparisonLabel: string;
}

function KpiDeltaPill({ delta, accent, comparisonLabel }: KpiDeltaPillProps) {
  if (!delta) {
    return null;
  }
  return (
    <DeltaPill
      delta={delta.value}
      increaseIsGood={delta.increaseIsGood}
      format={(absolute) => formatDeltaMagnitude(delta.unit, absolute)}
      surface={accent ? "accent" : "default"}
      comparisonLabel={comparisonLabel}
    />
  );
}
