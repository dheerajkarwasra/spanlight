/**
 * Project, trace, span, session, metrics, price and export types, mirroring the backend's bodies
 * exactly (snake_case; money as a decimal string; every unknown value `null`, never zero).
 * Re-exported through `types.ts`.
 */

import type { AttributionGroupBy } from "./attribution-types";
import type { User } from "./identity-types";
import type { ScoreSummary } from "./scores";
import type { Page } from "./types";

export interface Project {
  id: string;
  org_id: string;
  name: string;
  slug: string;
  retention_days: number;
  capture_payloads: boolean;
  /** The Monday summary email to the project's members. Absent from older servers: read it as on. */
  weekly_digest_enabled?: boolean;
  /** Alert channels of the org that hear about critical insights; `[]` is off. */
  insight_channel_ids: string[];
  /** Metadata keys the daily attribution rollup keeps, in the order they were set. */
  promoted_metadata_keys: string[];
  created_at: string;
}

export interface ProjectUpdate {
  name?: string;
  retention_days?: number;
  capture_payloads?: boolean;
  weekly_digest_enabled?: boolean;
  /** At most 10 channels of the project's org (else `422 UNKNOWN_CHANNEL`); `[]` turns it off. */
  insight_channel_ids?: string[];
  /**
   * At most 5 metadata keys, order kept; `[]` promotes none. A new key is backfilled over the
   * last 7 UTC days.
   */
  promoted_metadata_keys?: string[];
}

/**
 * What a project API key may do. The dashboard offers `ingest:write` and `traces:read`; the other
 * two are reserved by the server and no route accepts them yet.
 */
export type KeyScope = "ingest:write" | "traces:read" | "scores:write" | "prompts:read";

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: KeyScope[];
  created_by: User | null;
  created_at: string;
  last_used_at: string | null;
  /** Null: the key never expires. */
  expires_at: string | null;
  revoked_at: string | null;
}

export interface CreatedApiKey extends ApiKey {
  secret: string;
}

export interface OnboardingStatus {
  has_traces: boolean;
  first_trace_at: string | null;
}

export type SpanKind = "llm" | "tool" | "retrieval" | "chain" | "agent" | "http" | "other";
export type SpanStatus = "ok" | "error" | "unset";
export type TraceStatusFilter = "ok" | "error";

/** The kind of failure a failed span is; filters the trace list and groups errors. */
export type ErrorClass =
  | "auth"
  | "rate_limit"
  | "timeout"
  | "context_length"
  | "content_filter"
  | "provider_5xx"
  | "network"
  | "client"
  | "unknown"
  /** Spanlight's own gateway refused or failed the call; never the provider's fault. */
  | "gateway";

/**
 * Which capacity limit refused a call: `gateway` is Spanlight's own per-key limiter, `overloaded`
 * a provider at capacity (a 529 or an overloaded 5xx), the rest the caller's provider quota.
 */
export type RateLimitKind =
  "requests" | "tokens" | "spend_cap" | "acceleration" | "overloaded" | "gateway" | "unknown";

/** The trace list's order: newest first, or a value sort (unknown values last either way). */
export type TraceSort = "started_at" | "duration" | "cost" | "tokens" | "retry_cost_usd";

/** Backend values: `stop`, `length`, `tool_calls`, `content_filter` or `other`. */
export type FinishReason = "stop" | "length" | "tool_calls" | "content_filter" | "other";

export interface TraceSummary {
  trace_id: string;
  name: string | null;
  environment: string | null;
  release: string | null;
  external_user_id: string | null;
  session_id: string | null;
  tags: string[];
  started_at: string;
  ended_at: string;
  duration_ms: number;
  span_count: number;
  error_count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string | null;
  has_unpriced: boolean;
  models: string[];
  /** Earliest failed span's status message; null when nothing failed or no message was sent. */
  error_message: string | null;
  /** The same span's error class; null when nothing failed or it failed before classification. */
  error_class: ErrorClass | null;
  /** True once the trace has any score, from a person, the SDK or an evaluator. */
  has_scores: boolean;
  /**
   * LLM calls of the same request that a later call replaced (cache hits and Lab faults left
   * out). Null for a trace stored before 2026-10-15 and not recomputed since.
   */
  retry_calls: number | null;
  /** The cost of those calls; null when any of them is unpriced or `retry_calls` is null. */
  retry_cost_usd: string | null;
  /** The latest score of each name, most recently scored first; at most 10 names. */
  scores: ScoreSummary[];
  /** The trace's metadata as stored: at most 32 keys, every value a string. */
  metadata: Record<string, string>;
  /**
   * Readable text of the trace's input and output, at most 200 characters. Filled only by the
   * list with `preview=true`; null otherwise or when nothing readable was captured.
   */
  input_preview?: string | null;
  output_preview?: string | null;
}

/** The tier a provider billed a call at, normalised from each provider's spelling. */
export type ServiceTier = "standard" | "batch" | "priority" | "flex";

/** Why a span has or lacks a cost; derived by the server on every read. */
export type UsageState =
  | "priced"
  | "rollup"
  | "usage_not_reported"
  | "partial_usage"
  | "model_unknown"
  | "no_price"
  | "not_applicable";

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  kind: SpanKind;
  name: string;
  status: SpanStatus;
  status_message: string | null;
  /** Why the span failed; null when it did not, or failed before classification. */
  error_class: ErrorClass | null;
  /** The error type the provider's envelope named; null when it succeeded or none was sent. */
  provider_error_type: string | null;
  /** The error code the provider's envelope carried; null when it succeeded or none was sent. */
  provider_error_code: string | null;
  /** Which capacity limit refused the call; null when it was not a capacity refusal. */
  rate_limit_kind: RateLimitKind | null;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  provider: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  /** Tokens written to the prompt cache; null when the provider did not report them. */
  cache_write_tokens: number | null;
  /** The part of `cache_write_tokens` written with the 1-hour TTL; null when not reported. */
  cache_write_1h_tokens: number | null;
  /** The tier the provider billed at; null when not reported (priced at the standard rate). */
  service_tier: ServiceTier | null;
  /** Web searches the provider ran for the call; null when not reported. */
  web_search_requests: number | null;
  /** The whole cost: token cost plus `fee_usd`. Null when the token cost is unknown. */
  cost_usd: string | null;
  /** The non-token part of `cost_usd` (web search fees); null when no fee was priced. */
  fee_usd: string | null;
  pricing_version: string | null;
  time_to_first_token_ms: number | null;
  input: unknown;
  output: unknown;
  attributes: Record<string, unknown>;
  truncated: boolean;
  /**
   * True when trace detail left `input` and `output` out (null) because the trace's payloads
   * passed its 8 MiB budget; `projectsApi.span` returns the span with them.
   */
  payload_omitted: boolean;
  /** Canonical finish reason; the raw value is in `attributes`. Null when unknown. */
  finish_reason: FinishReason | null;
  /** The prompt version that produced this call; null when it was not linked to one. */
  prompt_version_id: string | null;
  /** The span repeats the usage of its child spans, so trace totals leave it out. */
  usage_rollup: boolean;
  usage_state: UsageState;
  /** Digest of the request body; equal on consecutive siblings means a retry. Null when not sent. */
  request_hash: string | null;
}

export interface TraceDetail extends TraceSummary {
  spans: Span[];
}

/** One side of a compared step: the span's stored values. */
export interface StepSpan {
  span_id: string;
  kind: SpanKind;
  status: SpanStatus;
  model: string | null;
  duration_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: string | null;
}

/** A span of trace A and its counterpart in trace B, paired by name and occurrence. */
export interface CompareStep {
  name: string;
  /** 1 for the first span of this name in its trace, 2 for the second. */
  occurrence: number;
  /** Null: the step is only in trace B. */
  a: StepSpan | null;
  /** Null: the step is only in trace A. */
  b: StepSpan | null;
  /** B minus A; null unless both sides have the value. */
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: string | null;
  /** Null when it cannot be known (a side missing, not captured, or truncated). */
  input_changed: boolean | null;
  output_changed: boolean | null;
}

export interface TraceComparison {
  a: TraceSummary;
  b: TraceSummary;
  /** B minus A over the whole traces; `cost_usd` null when either has an unpriced call. */
  totals: {
    duration_ms: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: string | null;
    span_count: number;
    error_count: number;
  };
  steps: CompareStep[];
  /** A trace has more than 2,000 spans; only the first 2,000 of each were compared. */
  truncated: boolean;
}

/**
 * One trace of a session with the few spans its conversation needs (root, first LLM call with an
 * input, last with an output, earliest failure). A span past the page's payload budget has
 * `payload_omitted` set.
 */
export interface SessionTurn {
  trace: TraceSummary;
  spans: Span[];
  /** The trace's tool spans, all of them (not only those in `spans`). */
  tool_calls: number;
}

export interface SessionSummary {
  session_id: string;
  trace_count: number;
  first_at: string;
  last_at: string;
  cost_usd: string | null;
  /** A trace in the session has an unpriced call, so a known `cost_usd` is a lower bound. */
  has_unpriced: boolean;
  error_count: number;
}

/** The sessions list's order; value sorts list unknown values last in both directions. */
export type SessionSort = "last" | "cost" | "turns" | "errors" | "tokens";

/** A `GET /sessions` row. */
export interface SessionListItem extends SessionSummary {
  /** The one user id every trace of the session names (traces without one left out), else null. */
  external_user_id: string | null;
  /** Distinct user ids among the session's traces. */
  user_count: number;
  input_tokens: number;
  output_tokens: number;
  /** From the first trace's start to the last trace's end. */
  duration_ms: number;
  /** The newest trace; null only when the session's traces were deleted while the page was read. */
  last_trace_id: string | null;
  /** The newest trace's input preview; null without `preview=true` or when none was captured. */
  last_input_preview?: string | null;
}

export interface SessionPage extends Page<SessionListItem> {
  /**
   * The 99th percentile of session cost over the window, whatever the search. First page only
   * (null on later pages) and null while fewer than 20 sessions have a known cost.
   */
  cost_p99_usd: string | null;
}

export interface Kpis {
  traces: number;
  llm_calls: number;
  error_rate: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  cost_usd: string | null;
  unpriced_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface OverviewMetrics {
  current: Kpis;
  previous: Kpis;
  /** True when read from hourly rollups (windows over 24 h): percentiles are estimates. */
  approximate: boolean;
}

export type Bucket = "hour" | "day";

/** Traces and failed traces per bucket for the trace list's filters (`GET …/traces/histogram`). */
export interface TraceHistogram {
  /** Hours for a window of 48 hours or less, else UTC days. */
  bucket: Bucket;
  /** Oldest first; buckets without a trace are left out. */
  buckets: { bucket_start: string; traces: number; errors: number }[];
}

export interface TimeseriesPoint {
  bucket_start: string;
  llm_calls: number;
  errors: number;
  p95_ms: number | null;
  cost_usd: string | null;
  tokens: number;
  approximate: boolean;
}

export interface ModelMetrics {
  provider: string | null;
  model: string | null;
  calls: number;
  errors: number;
  p50_ms: number | null;
  p95_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string | null;
  approximate: boolean;
}

export interface Price {
  provider: string;
  /** Matches a model exactly or with a snapshot suffix (`-YYYYMMDD`, `-latest`). */
  model_pattern: string;
  /** USD per million tokens, as decimal strings. */
  input_per_mtok: string;
  output_per_mtok: string;
  /** Null when the provider has no separate cached-input price. */
  cached_input_per_mtok: string | null;
  /** Cache-write rates per TTL; null when writes are billed at the input rate. */
  cache_write_5m_per_mtok: string | null;
  cache_write_1h_per_mtok: string | null;
  /** The smallest prompt (in tokens) this row prices; 0 is the base tier. */
  min_prompt_tokens: number;
  effective_from: string;
  version: string;
}

/** A model with LLM usage in a window whose calls got no cost for want of a price. */
export interface UnpricedModel {
  provider: string | null;
  model: string;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export type ExportFormat = "jsonl" | "csv";
export type ExportStatus = "queued" | "running" | "done" | "failed" | "expired";

/** An attribution export's window (at most 90 days) and environment. */
export interface AttributionExportFilters {
  from: string;
  to: string;
  environment?: string | null;
}

/**
 * The trace-list filters an export applies. `from` and `to` are required and at most 90 days
 * apart. The server returns the filters it stored with `null` for those that were not set.
 */
export interface ExportFilters extends AttributionExportFilters {
  release?: string | null;
  model?: string | null;
  status?: TraceStatusFilter | null;
  error_class?: ErrorClass | null;
  user_id?: string | null;
  session_id?: string | null;
  tag?: string | null;
  /** With `metadata_value`, the traces carrying the pair; alone, the traces without the key. */
  metadata_key?: string | null;
  metadata_value?: string | null;
  q?: string | null;
  /** A selection from the trace list (1 to 200 ids); the window and other filters still apply. */
  trace_ids?: string[] | null;
}

export type ExportKind = "traces" | "attribution";

export interface TraceExport {
  id: string;
  kind: ExportKind;
  format: ExportFormat;
  /** An attribution export carries only the window and environment. */
  filters: ExportFilters;
  /** Set on an attribution export, with `key` when the group is a metadata key. */
  group_by: AttributionGroupBy | null;
  key: string | null;
  status: ExportStatus;
  row_count: number | null;
  size_bytes: number | null;
  /** Set when `status` is `failed`, e.g. `EXPORT_TOO_LARGE`, `EXPORT_TIMEOUT` or `EXPORT_FAILED`. */
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  /** When the file is deleted; null until the export is done. */
  expires_at: string | null;
  /** A presigned link valid for one hour, present only while `status` is `done`. */
  download_url: string | null;
}

export interface FilterOptions {
  environments: string[];
  releases: string[];
  models: string[];
  tags: string[];
  /** Score names written in the last 30 days. */
  score_names: string[];
  /** Metadata keys seen on the project's traces in the last 7 days; at most 100. */
  metadata_keys: string[];
  /** Trace names seen on the project's traces in the last 7 days; at most 100. */
  trace_names: string[];
}
