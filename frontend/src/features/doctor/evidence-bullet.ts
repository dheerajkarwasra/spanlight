import type { Insight } from "@/lib/api";
import { formatCost, formatDuration, formatPercent } from "@/lib/format";

import { metricLabel } from "./evidence-metrics";
import { windowLength } from "./insight-format";

/*
 * The finding's lead chart: the value the detector saw against its stored baseline, for the kinds
 * whose evidence holds both (spec ruling 14). Findings keep no series and no usual range, so the
 * chart is a bar for the observed value and a tick for the baseline, nothing more.
 */

export type BulletUnit = "pct" | "ms" | "usd";

export interface Bullet {
  label: string;
  observed: number;
  baseline: number;
  unit: BulletUnit;
  /** "15 min window". */
  windowLabel: string;
}

/** Per kind: the observed metric, its baseline metric and the unit both share. */
export const BULLET_METRICS: Readonly<
  Record<string, { observed: string; baseline: string; unit: BulletUnit }>
> = {
  error_spike: { observed: "error_rate", baseline: "baseline_error_rate", unit: "pct" },
  latency_regression: { observed: "p95_ms", baseline: "baseline_p95_ms", unit: "ms" },
  cost_spike: { observed: "spend_usd", baseline: "baseline_hourly_usd", unit: "usd" },
};

function finite(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** The chart for a finding, or null when its kind has no baseline or a number is unreadable. */
export function bulletFor(insight: Pick<Insight, "kind" | "evidence">): Bullet | null {
  const spec = BULLET_METRICS[insight.kind];
  if (spec === undefined) {
    return null;
  }
  const { metrics, window } = insight.evidence;
  const observed = finite(metrics[spec.observed]);
  const baseline = finite(metrics[spec.baseline]);
  const windowLabel = windowLength(window.start, window.end);
  if (observed === null || baseline === null || windowLabel === null) {
    return null;
  }
  return { label: metricLabel(spec.observed), observed, baseline, unit: spec.unit, windowLabel };
}

/** A value in the bullet's unit: "18.2%", "4.20 s", "$12.50". */
export function formatBulletValue(value: number, unit: BulletUnit): string {
  const text =
    unit === "pct"
      ? formatPercent(value)
      : unit === "ms"
        ? formatDuration(value)
        : formatCost(value);
  // Every value reaching here is finite, so the formatters always answer.
  return text ?? String(value);
}

/**
 * Every charted detector's baseline: the 7 days that end where the 24-hour detection window
 * starts (`backend/app/insights/context_queries.py`).
 */
export const BASELINE_PERIOD = "the 7 days before the last 24 hours";

/**
 * "Error rate 18.2% vs baseline 2.1% from the 7 days before the last 24 hours · 15 min window":
 * the chart's text equivalent.
 */
export function bulletSentence(bullet: Bullet): string {
  const observed = formatBulletValue(bullet.observed, bullet.unit);
  const baseline = formatBulletValue(bullet.baseline, bullet.unit);
  return `${bullet.label} ${observed} vs baseline ${baseline} from ${BASELINE_PERIOD} · ${bullet.windowLabel}`;
}

/** Room past the larger value, so the bar never ends flush with the track. */
const HEADROOM = 1.25;

/** Both values as a percent of the track, on one scale from zero. */
export function bulletPositions(values: Pick<Bullet, "observed" | "baseline">): {
  observed: number;
  baseline: number;
} {
  const max = Math.max(values.observed, values.baseline, 0) * HEADROOM;
  if (max === 0) {
    return { observed: 0, baseline: 0 };
  }
  const place = (value: number) => (Math.max(0, value) / max) * 100;
  return { observed: place(values.observed), baseline: place(values.baseline) };
}
