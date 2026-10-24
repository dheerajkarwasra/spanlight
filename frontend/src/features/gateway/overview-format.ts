import type { DeltaPillProps } from "@/components/delta-pill";
import { GATEWAY_TIME_RANGE_OPTIONS } from "@/features/shell";
import type { FaultScenario, FaultScenarioCount, GatewayOverview, ProviderKind } from "@/lib/api";
import {
  formatDuration,
  formatInteger,
  formatPercent,
  pluralize,
  relativeChange,
} from "@/lib/format";
import { resolveRange, type RangeValue, type ResolvedRange } from "@/lib/time-range";

/** The overview endpoint rejects a window longer than this. */
export const MAX_OVERVIEW_WINDOW_MS = 7 * 24 * 3600_000;

/** Shown wherever a ratio or percentile has no request behind it. */
export const NO_REQUESTS_REASON = "No requests in this window.";

/** Requests ran, but none recorded the gateway's overhead (cache hits and refusals don't). */
const NO_OVERHEAD_REASON = "No gateway overhead recorded.";

/**
 * The window the overview reads. The shared time-range state may hold 30 days or a custom range
 * from another page; the gateway picker offers neither, so those read as its 7-day fallback, the
 * preset the picker shows. The URL is left alone so the other pages keep their range.
 */
export function gatewayWindow(range: ResolvedRange): ResolvedRange {
  const offered = GATEWAY_TIME_RANGE_OPTIONS.presets.some((preset) => preset === range.value);
  return offered && range.durationMs <= MAX_OVERVIEW_WINDOW_MS
    ? range
    : resolveRange({ range: GATEWAY_TIME_RANGE_OPTIONS.fallback });
}

/** "last 24 h": the window as it reads in a KPI line. A no-break space keeps "24" and "h" together. */
export function windowPhrase(value: RangeValue): string {
  const length = RANGE_LENGTHS[value];
  return length ? `last ${length.replace(" ", "\u00a0")}` : "this window";
}

/** Share of requests as a percentage; null when there are no requests to take a share of. */
export function formatErrorShare(errors: number, requests: number): string | null {
  return requests > 0 ? formatPercent(errors / requests) : null;
}

/** p95 times and overhead, in the shared duration format. */
export function formatMs(ms: number | null): string | null {
  return formatDuration(ms);
}

/**
 * Lab scenarios that end the call in an error: the gateway answers with the fault itself.
 * `malformed_json`, `truncated_stream` and `slow_response` alter a real provider response on its
 * way back, so those spans end with status ok and are not in the error count. An allow-list, so a
 * scenario added later is not counted by default.
 */
const FAILING_SCENARIOS: ReadonlySet<FaultScenario> = new Set([
  "auth_expired",
  "scope_denied",
  "rate_limited",
  "unsupported_parameter",
  "provider_5xx",
  "timeout",
]);

/** How many of the window's errors Lab faults caused on purpose. */
export function labErrorCount(faults: readonly FaultScenarioCount[]): number {
  return faults
    .filter((fault) => FAILING_SCENARIOS.has(fault.scenario))
    .reduce((sum, fault) => sum + fault.count, 0);
}

/** Lab fault counts, the most frequent scenario first. */
export function sortedFaults(faults: readonly FaultScenarioCount[]): FaultScenarioCount[] {
  return [...faults].sort((a, b) => b.count - a.count || a.scenario.localeCompare(b.scenario));
}

const PROVIDER_LABELS: Record<ProviderKind, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI-compatible",
};

export function providerLabel(provider: ProviderKind | null): string | null {
  return provider === null ? null : PROVIDER_LABELS[provider];
}

export type GatewayKpiId = "requests" | "latency" | "cache_hit_rate" | "fallbacks";

/** A line under a KPI value: plain text, or a labelled value that may be unknown. */
export type GatewayKpiLine = string | { prefix: string; value: string | null; reason: string };

export interface GatewayKpi {
  id: GatewayKpiId;
  label: string;
  /** Formatted value; null renders as "—" with `unknownReason`. */
  value: string | null;
  unknownReason: string;
  /** Change versus the previous window; undefined when there is nothing to compare with. */
  delta: DeltaPillProps | undefined;
  /** The first line is the card's denominator; the others follow it. */
  lines: GatewayKpiLine[];
}

const RANGE_LENGTHS: Record<RangeValue, string | null> = {
  "1h": "1 h",
  "24h": "24 h",
  "7d": "7 d",
  "30d": "30 d",
  custom: null,
};

/** Rounded so a vanishing change reads as flat, never "−0.0". */
function rounded(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor + 0;
}

/** The four ribbon cards (spec ruling 20), each with its delta against the previous window. */
export function buildGatewayKpis(overview: GatewayOverview, window: RangeValue): GatewayKpi[] {
  const { requests, errors, cache, previous } = overview;
  const noRequests = requests === 0;
  const fromLab = labErrorCount(overview.faults);
  const length = RANGE_LENGTHS[window];
  const comparisonLabel = length ? `vs previous ${length}` : "vs previous window";
  // A previous window without gateway traffic has nothing to compare with: no pill at all.
  const compared = previous.requests > 0;

  function delta(
    change: number | null,
    increaseIsGood: boolean | null,
    format?: (absolute: number) => string,
  ): DeltaPillProps | undefined {
    return compared && change !== null
      ? { delta: change, increaseIsGood, format, comparisonLabel, showArrow: true }
      : undefined;
  }
  const change = (current: number | null, before: number | null) =>
    current === null || before === null ? null : current - before;
  const requestsChange = relativeChange(requests, previous.requests);
  const overheadChange = change(overview.p95_overhead_ms, previous.p95_overhead_ms);
  const cacheChange = change(cache.hit_rate, previous.cache_hit_rate);

  return [
    {
      id: "requests",
      label: "Requests",
      value: formatInteger(requests),
      unknownReason: NO_REQUESTS_REASON,
      delta: delta(requestsChange === null ? null : rounded(requestsChange * 100, 1), null),
      lines: [
        overview.error_rate === null
          ? windowPhrase(window)
          : `${formatPercent(1 - overview.error_rate) ?? ""} success · ${pluralize(errors, "error")}`,
        ...(fromLab > 0 ? [`${pluralize(fromLab, "error")} from Lab`] : []),
      ],
    },
    {
      id: "latency",
      label: "Latency",
      value: formatMs(overview.p95_overhead_ms),
      unknownReason: noRequests ? NO_REQUESTS_REASON : NO_OVERHEAD_REASON,
      delta: delta(
        overheadChange === null ? null : rounded(overheadChange, 0),
        false,
        (absolute) => formatMs(absolute) ?? "",
      ),
      lines: [
        {
          prefix: "Upstream p95",
          value: formatMs(overview.p95_upstream_ms),
          reason: noRequests ? NO_REQUESTS_REASON : NO_OVERHEAD_REASON,
        },
        {
          prefix: "TTFT p95",
          value: formatMs(overview.p95_ttft_ms),
          reason: noRequests ? NO_REQUESTS_REASON : "No streaming requests in this window.",
        },
      ],
    },
    {
      id: "cache_hit_rate",
      label: "Cache hit rate",
      value: formatPercent(cache.hit_rate),
      unknownReason: noRequests ? NO_REQUESTS_REASON : "No cache lookups in this window.",
      delta: delta(
        cacheChange === null ? null : rounded(cacheChange * 100, 1),
        true,
        (absolute) => `${absolute.toFixed(1)} pt`,
      ),
      lines: [
        `${formatInteger(cache.hits) ?? "0"} hits · ${formatInteger(cache.misses) ?? "0"} misses`,
      ],
    },
    {
      id: "fallbacks",
      label: "Fallbacks",
      value: formatInteger(overview.fallbacks),
      unknownReason: NO_REQUESTS_REASON,
      delta: delta(
        overview.fallbacks - previous.fallbacks,
        false,
        (absolute) => formatInteger(absolute) ?? "",
      ),
      lines: [
        `${formatInteger(overview.rate_limited) ?? "0"} rate-limited · ${pluralize(overview.retries, "retry", "retries")}`,
      ],
    },
  ];
}
