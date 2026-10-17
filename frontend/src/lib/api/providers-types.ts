/**
 * Provider health, provider incident and telemetry completeness types, mirroring the backend's
 * bodies (`app/api/schemas/providers.py`, `completeness.py`) exactly: snake_case, rates and money
 * as decimal strings, every unknown value `null`, never zero. Re-exported through `types.ts`.
 */

import type { Bucket, ErrorClass, RateLimitKind } from "./telemetry-types";

/* ---------- Provider health ---------- */

export interface ProviderWindow {
  start: string;
  end: string;
}

export interface ErrorClassCount {
  error_class: ErrorClass;
  count: number;
}

export interface RateLimitKindCount {
  kind: RateLimitKind;
  count: number;
  /** How many of these refusals carried a provider `retry-after`. */
  with_retry_after: number;
}

/** A provider status page incident that overlapped the window and touched the API. */
export interface IncidentRef {
  id: string;
  title: string;
  impact: string;
  status: string;
  started_at: string;
  /** Null while the incident is open. */
  resolved_at: string | null;
  url: string;
}

export interface ProviderHealth {
  /** Null for LLM calls whose provider is unknown. */
  provider: string | null;
  llm_calls: number;
  /** Failed LLM calls, gateway Lab fault injections left out. */
  errors: number;
  /** `errors / llm_calls` as a decimal string; null without calls. */
  error_rate: string | null;
  /** Classes with failures, in the page's fixed order. */
  by_class: ErrorClassCount[];
  rate_limit_kinds: RateLimitKindCount[];
  /** Retried calls; null when any trace's count is unknown (stored before retries were counted). */
  retry_calls: number | null;
  /** Null when any trace's retry cost is unknown (`retry_cost_unknown`). */
  retry_cost_usd: string | null;
  retry_cost_unknown: boolean;
  incidents: IncidentRef[];
}

export interface HealthReport {
  window: ProviderWindow;
  /** Whole hours were read from the hourly rollups, as of their last run. */
  approximate: boolean;
  /** Busiest first. */
  providers: ProviderHealth[];
  /** How retries are attributed, for the page's footnote. */
  retry_cost_note: string;
}

export interface ProviderHealthQuery {
  from?: string | undefined;
  to?: string | undefined;
  environment?: string | undefined;
}

/* ---------- Errors over time and samples ---------- */

export interface ErrorBucket {
  bucket_start: string;
  /** Failed LLM calls per class; classes with no failures are absent. */
  by_class: Partial<Record<ErrorClass, number>>;
  /** The bucket holds an hour read from the hourly rollup. */
  approximate: boolean;
}

export interface ErrorTimeseriesQuery extends ProviderHealthQuery {
  bucket: Bucket;
  provider?: string | undefined;
}

export interface ErrorSample {
  trace_id: string;
  span_id: string;
  started_at: string;
  model: string | null;
  status_message: string | null;
  error_class: ErrorClass;
  rate_limit_kind: RateLimitKind | null;
  provider_error_type: string | null;
  provider_error_code: string | null;
  retry_after_s: number | null;
  /** Gateway attempts behind the call; null for calls that did not go through the gateway. */
  attempts: number | null;
  request_id: string | null;
}

export interface ErrorSamplesPage {
  items: ErrorSample[];
  next_cursor: string | null;
  /** The window was longer than 24 hours and was cut to its last 24 hours. */
  clamped: boolean;
}

export interface ErrorSamplesQuery extends ProviderHealthQuery {
  provider?: string | undefined;
  error_class?: ErrorClass | undefined;
  rate_limit_kind?: RateLimitKind | undefined;
  /** 1 to 50; the server's default is 20. */
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

/* ---------- Provider incidents ---------- */

/** The providers whose status pages the server polls. */
export type StatusProvider = "anthropic" | "openai" | "google";

export interface ProviderIncident {
  id: string;
  provider: string;
  title: string;
  impact: string;
  status: string;
  api_affected: boolean;
  components: string[];
  started_at: string;
  resolved_at: string | null;
  url: string;
  latest_update: string | null;
}

/** One status feed's poll state. */
export interface StatusSource {
  provider: string;
  last_success_at: string | null;
  last_polled_at: string | null;
  last_outcome: string | null;
  consecutive_failures: number;
}

export interface IncidentList {
  /** Whether this server polls the feeds at all (`PROVIDER_STATUS_POLLING_ENABLED`). */
  polling: "enabled" | "disabled";
  sources: StatusSource[];
  /** Newest first. */
  items: ProviderIncident[];
  next_cursor: string | null;
}

export interface IncidentListQuery {
  from?: string | undefined;
  to?: string | undefined;
  provider?: StatusProvider | undefined;
  /** The server's default (true) keeps the incidents that touch the providers' APIs. */
  api_only?: boolean | undefined;
  /** At most 100; the server's default is 50. */
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

/* ---------- Telemetry completeness ---------- */

export interface TelemetryLoss {
  /** Where the spans were lost: the gateway, ingest, or the SDK. */
  source: string;
  reason: string;
  /** Clamped to 2^53 - 1. */
  count: number;
}

export interface Completeness {
  /** Clamped to 2^53 - 1. */
  stored_spans: number;
  losses: TelemetryLoss[];
  /**
   * `stored / (stored + every loss)` to four places, rounded down, as a decimal string; null when
   * nothing was stored and nothing lost, `"0"` when only losses were counted.
   */
  completeness: string | null;
  /** SDK `failed` and ingest rejections were both counted, so the ratio may be lower than true. */
  lower_bound: boolean;
}

export interface CompletenessQuery {
  from?: string | undefined;
  to?: string | undefined;
}
