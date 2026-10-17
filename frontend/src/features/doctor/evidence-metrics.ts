import { formatCost, formatDuration, formatInteger, formatPercent } from "@/lib/format";

import { isIncidentMetric } from "./incident-citation";

/*
 * Evidence metrics arrive as a name and a decimal string, an integer or null. The name says what
 * the number is, so it picks the unit: rates and shares are fractions shown as percents,
 * `*_ms` and `*_s` are durations, `*_usd` is money, `ratio` is a multiple, the rest are counts.
 */

const LABELS: Record<string, string> = {
  error_rate: "Error rate",
  baseline_error_rate: "Baseline error rate",
  calls: "LLM calls",
  errors: "Errors",
  p95_ms: "p95 latency",
  baseline_p95_ms: "Baseline p95",
  spend_usd: "Spend",
  baseline_hourly_usd: "Baseline per hour",
  ratio: "Times the baseline",
  priced_calls: "Priced calls",
  storms: "Storms",
  largest_storm: "Largest storm",
  hashes: "Distinct requests",
  pairs: "Early retries",
  median_early_by_s: "Median early by",
  share: "Share",
  rate_limited: "Rate limited",
  truncated: "Truncated",
  sessions: "Sessions",
  max_last_input_tokens: "Largest last input",
  repeated_hashes: "Repeated requests",
  repeated_calls: "Repeated calls",
  estimated_input_tokens: "Estimated input tokens",
  traces: "Traces",
  max_repeats: "Longest repeat",
  rounds: "Rounds",
  aborts: "Aborted calls",
  clustered_at_ms: "Cut off at",
  success_p95_ms: "Successful p95",
  input_tokens: "Input tokens",
  output_tokens: "Output tokens",
  org_errors: "Errors org-wide",
  org_share: "Share org-wide",
  projects_affected: "Projects affected",
};

/** Short phrases under the number, for the metrics whose meaning is not obvious from the name. */
const HINTS: Record<string, string> = {
  storms: "bursts in the window",
  largest_storm: "same request, within 60 s",
  hashes: "request hashes",
  baseline_error_rate: "previous 7 days",
  baseline_p95_ms: "previous 7 days",
  baseline_hourly_usd: "previous 7 days",
};

export function metricLabel(name: string): string {
  const known = LABELS[name];
  if (known !== undefined) {
    return known;
  }
  const spaced = name.replaceAll("_", " ").trim();
  return spaced === "" ? name : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function metricHint(name: string): string | null {
  return HINTS[name] ?? null;
}

/** The metric as the tile shows it; null when it is unknown or unreadable (shown as "—"). */
export function formatMetric(name: string, value: string | number | null): string | null {
  if (value === null || value === "") {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) {
    return typeof value === "string" ? value : null;
  }
  if (name.endsWith("_usd")) {
    return formatCost(value);
  }
  if (name.endsWith("rate") || name.endsWith("share")) {
    return formatPercent(parsed);
  }
  if (name.endsWith("_ms")) {
    return formatDuration(parsed);
  }
  if (name.endsWith("_s")) {
    return formatDuration(parsed * 1000);
  }
  if (name === "ratio") {
    return `${parsed.toFixed(1)}×`;
  }
  return formatInteger(parsed);
}

export interface MetricTile {
  name: string;
  label: string;
  /** Null renders as "—" with a tooltip. */
  value: string | null;
  hint: string | null;
}

/** One tile per metric, except the provider-incident citation (drawn as its own line). */
export function metricTiles(
  metrics: Readonly<Record<string, string | number | null>>,
): MetricTile[] {
  return Object.entries(metrics)
    .filter(([name]) => !isIncidentMetric(name))
    .map(([name, value]) => ({
      name,
      label: metricLabel(name),
      value: formatMetric(name, value),
      hint: metricHint(name),
    }));
}
