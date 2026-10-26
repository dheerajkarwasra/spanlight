import type { Health, OverviewMetrics } from "@/lib/api";
import {
  formatCompact,
  formatCost,
  formatDuration,
  formatPercent,
  parseMoney,
  pluralize,
  relativeChange,
} from "@/lib/format";
import type { ResolvedRange } from "@/lib/time-range";

import type { ChartPoint } from "./chart-data";
import { NO_SCORE_REASON } from "./doctor-format";
import { errorCountFrom } from "./hero";
import { formatCompactCost, formatTileCost, projectMonthlySpend } from "./spend";

/** How a change is measured: relative percent, percentage points or milliseconds. */
export type DeltaUnit = "percent" | "points" | "ms";

export interface KpiDelta {
  /** Signed change, already rounded to the precision it is shown with. */
  value: number;
  unit: DeltaUnit;
  /**
   * Whether a rise is good news. Null for volume metrics (calls, tokens), where a change is
   * neither good nor bad and the pill stays neutral.
   */
  increaseIsGood: boolean | null;
}

const NO_LLM_CALLS = "No LLM calls in this period";

const PREVIOUS_PERIOD_LABELS: Record<ResolvedRange["value"], string> = {
  "1h": "previous hour",
  "24h": "previous 24h",
  "7d": "previous 7 days",
  "30d": "previous 30 days",
  custom: "previous period",
};

/** "previous 24h" etc. — the comparison window used for every delta. */
export function previousPeriodLabel(range: Pick<ResolvedRange, "value">): string {
  return PREVIOUS_PERIOD_LABELS[range.value];
}

function roundTo(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  // `+ 0` turns −0 into 0 so a vanishing change reads as flat, not "−0.0%".
  return Math.round(value * factor) / factor + 0;
}

/** Relative change in percent (12.4 for +12.4%), or null when the previous window is 0 or unknown. */
export function percentDelta(current: number | null, previous: number | null): number | null {
  const ratio = relativeChange(current, previous);
  return ratio === null ? null : roundTo(ratio * 100, 1);
}

/** Change of a rate in percentage points (−0.1 for 0.52% → 0.42%). */
export function pointsDelta(current: number | null, previous: number | null): number | null {
  if (current === null || previous === null) {
    return null;
  }
  return roundTo((current - previous) * 100, 1);
}

/** Absolute change in whole milliseconds. */
export function msDelta(current: number | null, previous: number | null): number | null {
  if (current === null || previous === null) {
    return null;
  }
  return roundTo(current - previous, 0);
}

/** The unsigned magnitude for a delta pill; DeltaPill adds the sign. */
export function formatDeltaMagnitude(unit: DeltaUnit, absolute: number): string {
  switch (unit) {
    case "percent":
      return `${absolute.toFixed(1)}%`;
    case "points":
      return `${absolute.toFixed(1)} pt`;
    case "ms":
      return formatDuration(absolute) ?? `${absolute} ms`;
  }
}

function delta(
  value: number | null,
  unit: DeltaUnit,
  increaseIsGood: boolean | null,
): KpiDelta | null {
  return value === null ? null : { value, unit, increaseIsGood };
}

export type RibbonKpiId = "calls" | "p95" | "spend" | "doctor";

/** One card of the KPI ribbon (spec §4 O1), formatted; the ribbon adds links and sparklines. */
export interface RibbonKpi {
  id: RibbonKpiId;
  label: string;
  /** Formatted value, or null when the metric is unknown for this window. */
  value: string | null;
  /** Why the value is unknown. Only shown when `value` is null. */
  unknownReason: string;
  /** The muted line under the value, e.g. "15 errors · 3.5%". */
  denominator: string | null;
  /** The value (and the p50 line) are estimates from hourly rollups. */
  approximate: boolean;
  /** Change versus the previous window, or null when there is nothing to compare. */
  delta: KpiDelta | null;
  /** Per-bucket values for the sparkline; null for a card without one. */
  trend: (number | null)[] | null;
  /** Spend only: some calls had no price, so the spend is a lower bound. */
  lowerBound: boolean;
  /** Spend only: the exact amount behind the short value ("$12,345.67" for "$12.3K"). */
  exact: string | null;
  /** Doctor only: open critical findings, shown in danger text after the denominator. */
  critical: number;
}

interface RibbonInput {
  metrics: OverviewMetrics;
  /** The UTC month's spend so far; null while it is unknown. */
  monthToDateUsd: number | null;
  /** The timeseries behind the sparklines; null while it is unknown. */
  points: readonly ChartPoint[] | null;
  /** The Doctor's score; null when it failed to load. */
  health: Health | null;
  range: Pick<ResolvedRange, "value" | "durationMs">;
  now: Date;
}

/**
 * Spend short enough for a half-width phone tile at 28 px: "$4.12", "$112", "$1.2K", "<$0.01".
 */
function tileCost(amount: number): string {
  return Math.abs(amount) >= 1000 ? formatCompactCost(amount) : formatTileCost(amount);
}

/** "Projected $X this month" for short windows, else the previous window's spend. */
function spendLine(
  projection: number | null,
  previousCost: number | null,
  range: Pick<ResolvedRange, "value">,
): string | null {
  if (projection !== null) {
    return `Projected ${formatCompactCost(projection)} this month`;
  }
  return previousCost === null
    ? null
    : `vs ${formatCost(previousCost) ?? ""} ${previousPeriodLabel(range)}`;
}

/** Calls · error rate, p95 latency, spend and the Doctor's score, in display order. */
export function ribbonKpis({
  metrics,
  monthToDateUsd,
  points,
  health,
  range,
  now,
}: RibbonInput): RibbonKpi[] {
  const { current, previous, approximate } = metrics;
  const errors = errorCountFrom(current.error_rate, current.llm_calls);
  const p50 = formatDuration(current.p50_ms);
  const cost = parseMoney(current.cost_usd);
  const previousCost = parseMoney(previous.cost_usd);
  const projection = projectMonthlySpend(cost, monthToDateUsd, points, range.durationMs, now);
  const open = health ? health.open_critical + health.open_warning : 0;
  const trend = (pick: (point: ChartPoint) => number | null) => points?.map(pick) ?? null;
  const base = {
    approximate: false,
    delta: null,
    trend: null,
    lowerBound: false,
    exact: null,
    critical: 0,
  };

  return [
    {
      ...base,
      id: "calls",
      label: "Calls · error rate",
      value: formatCompact(current.llm_calls),
      unknownReason: "Not reported for this period",
      denominator:
        errors === null
          ? null
          : `${pluralize(errors, "error")} · ${formatPercent(current.error_rate) ?? ""}`,
      delta: delta(percentDelta(current.llm_calls, previous.llm_calls), "percent", null),
      trend: trend((point) => point.llmCalls),
    },
    {
      ...base,
      id: "p95",
      label: "p95 latency",
      value: formatDuration(current.p95_ms),
      unknownReason: NO_LLM_CALLS,
      denominator: p50 === null ? null : `p50 ${approximate ? "≈ " : ""}${p50}`,
      approximate,
      delta: delta(msDelta(current.p95_ms, previous.p95_ms), "ms", false),
      trend: trend((point) => point.p95Ms),
    },
    {
      ...base,
      id: "spend",
      label: "Spend",
      value: cost === null ? null : tileCost(cost),
      exact: formatCost(cost),
      unknownReason: "No price for any model in this period",
      denominator: spendLine(projection, previousCost, range),
      delta: delta(percentDelta(cost, previousCost), "percent", false),
      lowerBound: cost !== null && current.unpriced_calls > 0,
    },
    {
      ...base,
      id: "doctor",
      label: "Doctor",
      value: health?.value == null ? null : String(health.value),
      unknownReason: health ? NO_SCORE_REASON : "The Doctor's score didn't load",
      denominator: health ? pluralize(open, "open finding") : null,
      critical: health?.open_critical ?? 0,
      approximate: health?.approximate ?? false,
    },
  ];
}
