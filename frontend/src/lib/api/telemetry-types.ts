/**
 * Project, trace, span, session, metrics, price and export types, mirroring the backend's bodies
 * exactly (snake_case; money as a decimal string; every unknown value `null`, never zero).
 * Re-exported through `types.ts`.
 */

import type { User } from "./identity-types";
import type { ScoreSummary } from "./scores";

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
  created_at: string;
}

export interface ProjectUpdate {
  name?: string;
  retention_days?: number;
  capture_payloads?: boolean;
  weekly_digest_enabled?: boolean;
  /** At most 10 channels of the project's org (else `422 UNKNOWN_CHANNEL`); `[]` turns it off. */
  insight_channel_ids?: string[];
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

export type SpanKind = "llm" | "tool" | "retrieval" | "chain" | "http" | "other";
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
  | "unknown";

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
  /** The latest score of each name, most recently scored first; at most 10 names. */
  scores: ScoreSummary[];
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  kind: SpanKind;
  name: string;
  status: SpanStatus;
  status_message: string | null;
  /** Why the span failed; null when it did not, or failed before classification. */
  error_class: ErrorClass | null;
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
  cost_usd: string | null;
  pricing_version: string | null;
  time_to_first_token_ms: number | null;
  input: unknown;
  output: unknown;
  attributes: Record<string, unknown>;
  truncated: boolean;
  /** Canonical finish reason; the raw value is in `attributes`. Null when unknown. */
  finish_reason: FinishReason | null;
  /** The prompt version that produced this call; null when it was not linked to one. */
  prompt_version_id: string | null;
}

export interface TraceDetail extends TraceSummary {
  spans: Span[];
}

export interface SessionSummary {
  session_id: string;
  trace_count: number;
  first_at: string;
  last_at: string;
  cost_usd: string | null;
  error_count: number;
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

/**
 * The trace-list filters an export applies. `from` and `to` are required and at most 90 days
 * apart. The server returns the filters it stored with `null` for those that were not set.
 */
export interface ExportFilters {
  from: string;
  to: string;
  environment?: string | null;
  release?: string | null;
  model?: string | null;
  status?: TraceStatusFilter | null;
  error_class?: ErrorClass | null;
  user_id?: string | null;
  session_id?: string | null;
  tag?: string | null;
  q?: string | null;
}

export interface TraceExport {
  id: string;
  format: ExportFormat;
  filters: ExportFilters;
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
}
