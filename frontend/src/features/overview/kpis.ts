import type { Kpis, OverviewMetrics } from "@/lib/api";
import {
  formatCompact,
  formatDuration,
  formatInteger,
  formatPercent,
  pluralNoun,
  relativeChange,
} from "@/lib/format";
import type { ResolvedRange } from "@/lib/time-range";

import type { ChartPoint } from "./chart-data";
import { errorCountFrom } from "./hero";

export type KpiId = "p95" | "error_rate" | "llm_calls" | "tokens";

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

export interface KpiCardModel {
  id: KpiId;
  label: string;
  /** Formatted value, or null when the metric is unknown for this window. */
  value: string | null;
  /** Why the value is unknown. Only shown when `value` is null. */
  unknownReason: string;
  /** The line under the value, e.g. "p50 640 ms" or "203 of 48.2K calls". */
  detail: string | null;
  /**
   * The p50 that `detail` reports on the p95 card ("640 ms"), kept apart so the card can put
   * the "≈" in front of the number. Null on the other cards and when the p50 is unknown.
   */
  p50: string | null;
  /**
   * True when this card's percentiles (value and `p50`) are estimates from hourly rollups.
   * Always false for counts, rates and tokens, which are exact.
   */
  approximate: boolean;
  /** Change versus the previous window, or null when there is nothing to compare. */
  delta: KpiDelta | null;
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

function totalTokens(kpis: Kpis): number {
  return kpis.input_tokens + kpis.output_tokens;
}

/** The four KPI cards next to the calls chart, in display order. */
export function buildKpiCards({ current, previous, approximate }: OverviewMetrics): KpiCardModel[] {
  const errors = errorCountFrom(current.error_rate, current.llm_calls);
  const callsLabel = formatCompact(current.llm_calls) ?? "0";
  const p50 = formatDuration(current.p50_ms);

  return [
    {
      id: "p95",
      label: "p95 latency",
      value: formatDuration(current.p95_ms),
      unknownReason: NO_LLM_CALLS,
      detail: p50 === null ? null : `p50 ${p50}`,
      p50,
      approximate,
      delta: delta(msDelta(current.p95_ms, previous.p95_ms), "ms", false),
    },
    {
      id: "error_rate",
      label: "Error rate",
      value: formatPercent(current.error_rate),
      unknownReason: NO_LLM_CALLS,
      detail:
        errors === null
          ? null
          : `${formatInteger(errors) ?? "0"} of ${callsLabel} ${pluralNoun(current.llm_calls, "call")}`,
      p50: null,
      approximate: false,
      delta: delta(pointsDelta(current.error_rate, previous.error_rate), "points", false),
    },
    {
      id: "llm_calls",
      label: "LLM calls",
      value: formatCompact(current.llm_calls),
      unknownReason: "Not reported for this period",
      detail: `across ${formatCompact(current.traces) ?? "0"} ${pluralNoun(current.traces, "trace")}`,
      p50: null,
      approximate: false,
      delta: delta(percentDelta(current.llm_calls, previous.llm_calls), "percent", null),
    },
    {
      id: "tokens",
      label: "Tokens",
      value: formatCompact(totalTokens(current)),
      unknownReason: "Not reported for this period",
      detail: `${formatCompact(current.input_tokens) ?? "0"} in · ${
        formatCompact(current.output_tokens) ?? "0"
      } out`,
      p50: null,
      approximate: false,
      delta: delta(percentDelta(totalTokens(current), totalTokens(previous)), "percent", null),
    },
  ];
}

/**
 * Counts and rates start their sparkline at zero, so a flat 0 % error rate draws along the bottom
 * instead of mid-box; latency keeps a min–max domain (its shape is what matters).
 */
export const KPI_ZERO_BASED: Record<KpiId, boolean> = {
  p95: false,
  error_rate: true,
  llm_calls: true,
  tokens: true,
};

/** Per-bucket values behind a KPI's sparkline, over the whole window. Unknown buckets stay null. */
export function kpiTrend(id: KpiId, points: readonly ChartPoint[]): (number | null)[] {
  switch (id) {
    case "p95":
      return points.map((point) => point.p95Ms);
    case "error_rate":
      return points.map((point) => (point.llmCalls > 0 ? point.errors / point.llmCalls : null));
    case "llm_calls":
      return points.map((point) => point.llmCalls);
    case "tokens":
      return points.map((point) => point.tokens);
  }
}
