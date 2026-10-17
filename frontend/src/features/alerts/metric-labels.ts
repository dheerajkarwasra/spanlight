import type { Comparator, Metric } from "@/lib/api";

/**
 * How each alert metric is named and written, matching the server's alert messages
 * (`app/alerts/formatting.py`): rates as percents with one decimal, latencies in ms or s, money in
 * dollars, counts whole. Numbers group thousands with a thin space, so "2 000 000" reads the same
 * in every locale. Every formatter returns null for an unknown or unreadable value, so the caller
 * renders the shared "—" instead of a fake zero.
 */

export type MetricUnit = "ratio" | "ms" | "usd" | "count";

export interface MetricLabel {
  /** Sentence case, for a heading or a settings row: "Error rate". */
  label: string;
  /** Inside a sentence, for the summary line: "error rate". */
  noun: string;
  unit: MetricUnit;
  /** True when the server may estimate the value from hourly histograms (percentiles only). */
  canBeApproximate: boolean;
}

export const METRIC_LABELS: Record<Metric, MetricLabel> = {
  error_rate: { label: "Error rate", noun: "error rate", unit: "ratio", canBeApproximate: false },
  p95_ms: { label: "p95 latency", noun: "p95 latency", unit: "ms", canBeApproximate: true },
  ttft_p95_ms: {
    label: "p95 time to first token",
    noun: "p95 time to first token",
    unit: "ms",
    canBeApproximate: true,
  },
  cost_usd: { label: "Cost", noun: "cost", unit: "usd", canBeApproximate: false },
  llm_calls: { label: "LLM calls", noun: "LLM calls", unit: "count", canBeApproximate: false },
  tokens: { label: "Tokens", noun: "tokens", unit: "count", canBeApproximate: false },
  error_calls: {
    label: "Failed LLM calls",
    noun: "failed LLM calls",
    unit: "count",
    canBeApproximate: false,
  },
};

/**
 * The metrics in the order the rule editor lists them; the two error metrics sit together
 * (Figma "Alerts/Rule editor/Error filters").
 */
export const METRICS: readonly Metric[] = [
  "error_rate",
  "error_calls",
  "p95_ms",
  "ttft_p95_ms",
  "cost_usd",
  "llm_calls",
  "tokens",
];

export const COMPARATOR_SYMBOLS: Record<Comparator, string> = {
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
};

export const COMPARATOR_WORDS: Record<Comparator, string> = {
  gt: "above",
  gte: "at or above",
  lt: "below",
  lte: "at or below",
};

/** Whether the comparator watches for a value going up (`>`, `≥`) rather than down. */
export function isUpward(comparator: Comparator): boolean {
  return comparator === "gt" || comparator === "gte";
}

const THIN_SPACE = " ";

function parseDecimal(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Up to `digits` decimals with trailing zeros dropped: 5 → "5", 6.80 → "6.8". */
function trimmed(value: number, digits: number): string {
  return String(Number(value.toFixed(digits)));
}

function groupThousands(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE);
}

function formatCount(value: number): string {
  const rounded = Math.round(value);
  const sign = rounded < 0 ? "-" : "";
  return sign + groupThousands(String(Math.abs(rounded)));
}

function formatRatio(value: number): string {
  return `${trimmed(value * 100, 1)} %`;
}

/** Below a second in ms ("820 ms"); from a second up in seconds with two decimals ("1.5 s"). */
function formatMs(value: number): string {
  if (Math.abs(value) < 1000) {
    return `${formatCount(value)} ms`;
  }
  return `${trimmed(value / 1000, 2)} s`;
}

/**
 * Whole dollars without cents ("$40"), otherwise two decimals, or four below a cent; a non-zero
 * amount too small for four decimals reads "< $0.0001", never a zero.
 */
function formatUsd(value: number): string {
  const absolute = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (absolute > 0 && absolute < 0.00005) {
    return `< ${sign}$0.0001`;
  }
  let text: string;
  if (Number.isInteger(absolute)) {
    text = String(absolute);
  } else if (absolute < 0.01) {
    text = absolute.toFixed(4);
  } else {
    text = absolute.toFixed(2);
  }
  const [whole = "0", fraction] = text.split(".");
  return `${sign}$${groupThousands(whole)}${fraction === undefined ? "" : `.${fraction}`}`;
}

const FORMATTERS: Record<MetricUnit, (value: number) => string> = {
  ratio: formatRatio,
  ms: formatMs,
  usd: formatUsd,
  count: formatCount,
};

interface FormatOptions {
  /** The server estimated the value from hourly histograms: prefix "≈". */
  approximate?: boolean;
}

/**
 * A metric value or threshold (a decimal string from the API) in the metric's unit: "6.8 %",
 * "1.5 s", "$14.60", "2 000 000". Null when the value is unknown.
 */
export function formatMetricValue(
  metric: Metric,
  value: string | null | undefined,
  options: FormatOptions = {},
): string | null {
  const parsed = parseDecimal(value);
  if (parsed === null) {
    return null;
  }
  const text = FORMATTERS[METRIC_LABELS[metric].unit](parsed);
  return options.approximate === true && METRIC_LABELS[metric].canBeApproximate ? `≈${text}` : text;
}
