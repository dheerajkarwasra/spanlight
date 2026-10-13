import { GATEWAY_TIME_RANGE_OPTIONS } from "@/features/shell";
import type { FaultScenario, FaultScenarioCount, GatewayOverview, ProviderKind } from "@/lib/api";
import { formatDuration, formatInteger, formatPercent } from "@/lib/format";
import { resolveRange, type RangeValue, type ResolvedRange } from "@/lib/time-range";

/** The overview endpoint rejects a window longer than this. */
export const MAX_OVERVIEW_WINDOW_MS = 7 * 24 * 3600_000;

/** Shown wherever a ratio or percentile has no request behind it. */
export const NO_REQUESTS_REASON = "No requests in this window.";

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

/** A no-break space keeps "24" and "h" on one line when a KPI detail wraps (T7-07). */
const WINDOW_PHRASES: Record<RangeValue, string> = {
  "1h": "last 1\u00a0h",
  "24h": "last 24\u00a0h",
  "7d": "last 7\u00a0d",
  "30d": "last 30\u00a0d",
  custom: "this window",
};

/** "last 24 h": the window as it reads in a KPI detail line. */
export function windowPhrase(value: RangeValue): string {
  return WINDOW_PHRASES[value];
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

export type GatewayKpiId =
  "requests" | "error_rate" | "cache_hit_rate" | "fallbacks" | "p95_overhead" | "p95_ttft";

export interface GatewayKpi {
  id: GatewayKpiId;
  label: string;
  /** Formatted value; null renders as "—" with `unknownReason`. */
  value: string | null;
  unknownReason: string;
  detail: string;
  /** A second line in the accent colour, e.g. "115 from Lab". */
  extra: string | null;
  /** Explains the metric behind an info button. */
  info: string | null;
}

/** The six KPI tiles, in the order of the frame. */
export function buildGatewayKpis(overview: GatewayOverview, window: RangeValue): GatewayKpi[] {
  const { requests, errors, cache } = overview;
  const noRequests = requests === 0;
  const fromLab = labErrorCount(overview.faults);
  const keyCount = overview.by_key.length;

  return [
    {
      id: "requests",
      label: "Requests",
      value: formatInteger(requests),
      unknownReason: NO_REQUESTS_REASON,
      detail: noRequests
        ? windowPhrase(window)
        : `across ${formatInteger(keyCount)} ${keyCount === 1 ? "key" : "keys"} · ${windowPhrase(window)}`,
      extra: null,
      info: null,
    },
    {
      id: "error_rate",
      label: "Error rate",
      value: formatPercent(overview.error_rate),
      unknownReason: NO_REQUESTS_REASON,
      detail: `${formatInteger(errors)} of ${formatInteger(requests)} requests`,
      extra: fromLab > 0 ? `${formatInteger(fromLab)} from Lab` : null,
      info: null,
    },
    {
      id: "cache_hit_rate",
      label: "Cache hit rate",
      value: formatPercent(cache.hit_rate),
      unknownReason: noRequests ? NO_REQUESTS_REASON : "No cache lookups in this window.",
      detail: `${formatInteger(cache.hits)} hits · ${formatInteger(cache.misses)} misses`,
      extra: null,
      info: null,
    },
    {
      id: "fallbacks",
      label: "Fallbacks",
      value: formatInteger(overview.fallbacks),
      unknownReason: NO_REQUESTS_REASON,
      detail: `plus ${formatInteger(overview.retries)} retries`,
      extra: null,
      info: null,
    },
    {
      id: "p95_overhead",
      label: "p95 overhead",
      value: formatMs(overview.p95_overhead_ms),
      unknownReason: NO_REQUESTS_REASON,
      detail: "added by Spanlight, per request",
      extra: null,
      info: "Time Spanlight adds on top of the provider, measured per request.",
    },
    {
      id: "p95_ttft",
      label: "p95 time to first token",
      value: formatMs(overview.p95_ttft_ms),
      unknownReason: noRequests ? NO_REQUESTS_REASON : "No streaming requests in this window.",
      detail: overview.p95_ttft_ms === null ? "0 streaming requests" : "streaming requests only",
      extra: null,
      info: null,
    },
  ];
}
