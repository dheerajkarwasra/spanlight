import {
  formatCompact,
  formatCost,
  formatDuration,
  formatInteger,
  formatPercent,
} from "@/lib/format";
import type { Comparison, ReleaseStats } from "@/lib/api";

export type MetricKind = "count" | "rate" | "duration" | "cost" | "tokens";
type MetricKey =
  | "traces"
  | "llm_calls"
  | "error_rate"
  | "p50_ms"
  | "p95_ms"
  | "cost_usd"
  | "input_tokens"
  | "output_tokens";

export interface MetricSpec {
  key: MetricKey;
  label: string;
  kind: MetricKind;
  /**
   * Errors and latency going up are bad. Volume (traces, calls, tokens) and total cost have no
   * tone: a total moves with traffic, so less traffic would read as a cost improvement.
   */
  increaseIsGood: boolean | null;
}

export const METRICS: readonly MetricSpec[] = [
  { key: "traces", label: "Traces", kind: "count", increaseIsGood: null },
  { key: "llm_calls", label: "LLM calls", kind: "count", increaseIsGood: null },
  { key: "error_rate", label: "Error rate", kind: "rate", increaseIsGood: false },
  { key: "p50_ms", label: "p50 latency", kind: "duration", increaseIsGood: false },
  { key: "p95_ms", label: "p95 latency", kind: "duration", increaseIsGood: false },
  { key: "cost_usd", label: "Cost", kind: "cost", increaseIsGood: null },
  { key: "input_tokens", label: "Input tokens", kind: "tokens", increaseIsGood: null },
  { key: "output_tokens", label: "Output tokens", kind: "tokens", increaseIsGood: null },
];

export const NO_COST_REASON =
  "No span in this release was priced (unknown model or no usage reported).";
export const NO_CALLS_REASON = "This release made no LLM calls in the window.";
const NO_DELTA_REASON = "Unknown for one of the two releases, so there is nothing to compare.";

export interface KpiRow {
  key: MetricKey;
  label: string;
  increaseIsGood: boolean | null;
  /** Formatted values; `null` renders the unknown placeholder with `unknownReason`. */
  a: string | null;
  b: string | null;
  /** Cost only: the API's decimal strings, for `CostValue` (which formats them itself). */
  aCost: string | null;
  bCost: string | null;
  /** Signed absolute change (`b - a`) as a number: sign for tone and arrow; `null` when unknown. */
  delta: number | null;
  /** The absolute change without its sign ("1,533", "0.89 pt"). */
  changeMagnitude: string | null;
  /** The relative change without its sign ("50 %", "<1 %"); `null` when `a` is 0 or unknown. */
  relativeMagnitude: string | null;
  unknownReason: string;
  /** Cost only: some calls have no price, so the shown value is a lower bound. */
  aLowerBound: boolean;
  bLowerBound: boolean;
}

function rawValue(stats: ReleaseStats, key: MetricKey): number | string | null {
  return stats[key];
}

function formatByKind(kind: MetricKind, value: number): string | null {
  switch (kind) {
    case "count":
      return formatInteger(value);
    case "rate":
      return formatPercent(value);
    case "duration":
      return formatDuration(value);
    case "cost":
      return formatCost(value);
    case "tokens":
      return formatCompact(value);
  }
}

/** The unit of an absolute change: an error-rate change is percentage points, not a percent. */
function formatAbsoluteMagnitude(kind: MetricKind, magnitude: number): string {
  if (kind === "rate") {
    return `${(magnitude * 100).toFixed(2)} pt`;
  }
  return formatByKind(kind, magnitude) ?? "—";
}

/** A release's value for display; `null` when the API has no value (never a made-up zero). */
export function formatMetric(stats: ReleaseStats, spec: MetricSpec): string | null {
  const raw = rawValue(stats, spec.key);
  if (raw === null) {
    return null;
  }
  const value = typeof raw === "string" ? Number(raw) : raw;
  return Number.isFinite(value) ? formatByKind(spec.kind, value) : null;
}

/**
 * A relative change as a fraction string (`"0.5"`) shown without its sign: "50 %", "2.8 %". A
 * non-zero change under 1 % reads "<1 %", never a rounded "0 %". `null` stays `null`.
 */
export function formatRelativeMagnitude(relative: string | null): string | null {
  if (relative === null) {
    return null;
  }
  const percent = Math.abs(Number(relative)) * 100;
  if (!Number.isFinite(percent)) {
    return null;
  }
  if (percent === 0) {
    return "0 %";
  }
  if (percent < 1) {
    return "<1 %";
  }
  return `${Number(percent.toFixed(percent < 10 ? 1 : 0))} %`;
}

function unknownReasonFor(kind: MetricKind, a: string | null, b: string | null): string {
  if (kind === "cost") {
    return NO_COST_REASON;
  }
  return a === null || b === null ? NO_CALLS_REASON : NO_DELTA_REASON;
}

/** The key-metrics table's rows, formatted, from one comparison. */
export function buildKpiRows(comparison: Comparison): KpiRow[] {
  return METRICS.map((spec) => {
    const delta = comparison.deltas[spec.key] ?? null;
    const a = formatMetric(comparison.a, spec);
    const b = formatMetric(comparison.b, spec);
    const isCost = spec.kind === "cost";
    const change = delta === null ? Number.NaN : Number(delta.absolute);
    return {
      key: spec.key,
      label: spec.label,
      increaseIsGood: spec.increaseIsGood,
      a,
      b,
      aCost: isCost ? comparison.a.cost_usd : null,
      bCost: isCost ? comparison.b.cost_usd : null,
      delta: Number.isFinite(change) ? change : null,
      changeMagnitude: Number.isFinite(change)
        ? formatAbsoluteMagnitude(spec.kind, Math.abs(change))
        : null,
      relativeMagnitude: delta === null ? null : formatRelativeMagnitude(delta.relative),
      unknownReason: unknownReasonFor(spec.kind, a, b),
      aLowerBound: isCost && comparison.a.unpriced_calls > 0,
      bLowerBound: isCost && comparison.b.unpriced_calls > 0,
    };
  });
}

/** Share of calls per model as a whole percent for labels; `null` when the release made no calls. */
export function formatShare(share: number | null): string | null {
  return share === null ? null : `${Math.round(share * 100)} %`;
}
