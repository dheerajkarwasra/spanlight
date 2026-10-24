/**
 * Release comparison and end-user analytics types, mirroring the backend (snake_case; money and
 * deltas as decimal strings; every unknown value `null`). Re-exported through `types.ts`.
 */

import type { ErrorClass, Page, SessionSummary } from "./types";

/* ---------- Releases ---------- */

export interface ReleaseStats {
  release: string;
  first_seen_at: string;
  last_seen_at: string;
  traces: number;
  llm_calls: number;
  unpriced_calls: number;
  /** Fraction 0 to 1; `null` without LLM calls. */
  error_rate: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  /** Sum of the priced spans: a lower bound while `unpriced_calls` is above 0. */
  cost_usd: string | null;
  input_tokens: number;
  output_tokens: number;
}

/** `b - a`, and that as a fraction of `a` (`null` when `a` is 0). */
export interface ReleaseDelta {
  absolute: string;
  relative: string | null;
}

export interface ModelShare {
  model: string | null;
  /** `null`: that release made no calls. */
  a_share: number | null;
  b_share: number | null;
}

export interface ErrorClassChange {
  /** `null`: failed spans the server could not classify. */
  error_class: ErrorClass | null;
  a_count: number;
  b_count: number;
}

/** A normalised failure message that spans of `b` have and spans of `a` do not. */
export interface NewError {
  message: string;
  count: number;
  example_trace_id: string;
}

export interface Comparison {
  a: ReleaseStats;
  b: ReleaseStats;
  /** Keyed by metric name; `null` when the metric is unknown for either release. */
  deltas: Record<string, ReleaseDelta | null>;
  model_mix: ModelShare[];
  error_classes: ErrorClassChange[];
  new_errors: NewError[];
}

export interface ReleaseListQuery {
  from?: string | undefined;
  to?: string | undefined;
  environment?: string | undefined;
}

export interface ReleaseCompareQuery extends ReleaseListQuery {
  /** Baseline release. */
  a: string;
  /** Candidate release. */
  b: string;
}

/** One failure category in one release: `count` of the reviewed traces, 4-place decimal strings. */
export interface CategoryRate {
  count: number;
  rate: string;
  /** The Wilson 95% interval on `rate`. */
  ci_low: string;
  ci_high: string;
}

export interface ReleaseFailureRates {
  /** `null` is the traces without a release. */
  release: string | null;
  /** Traces of the release in the window whose latest `failure_category` score was read. */
  reviewed: number;
  /** Keyed by category; empty when nothing was reviewed (unknown, never 0%). */
  rates: Record<string, CategoryRate>;
}

/**
 * The rate of each failure category per release. `categories` is the config's list in its order;
 * a label no longer in it still appears in `rates`, after the configured ones.
 */
export interface FailureCategoryRates {
  score_name: string;
  categories: string[];
  /** The same releases, in the same order, as the release list. */
  releases: ReleaseFailureRates[];
}

/* ---------- End users ---------- */

export type UserSort = "cost" | "errors" | "traces";

export interface UserStats {
  external_user_id: string;
  traces: number;
  llm_calls: number;
  /** Failed LLM calls. */
  errors: number;
  unpriced_calls: number;
  /** A lower bound while `unpriced_calls` is above 0; `null` when no call was priced. */
  cost_usd: string | null;
  tokens: number;
  /** `YYYY-MM-DD`, the first and last UTC day with activity in the window. */
  first_seen_day: string;
  last_seen_day: string;
}

export interface UserDayStats {
  /** `YYYY-MM-DD` (UTC). */
  day: string;
  traces: number;
  llm_calls: number;
  errors: number;
  unpriced_calls: number;
  cost_usd: string | null;
  tokens: number;
}

/** The requested window widened to whole UTC days. */
export interface UserStatsWindow {
  start: string;
  end: string;
}

export interface UserList extends Page<UserStats> {
  window: UserStatsWindow;
  /** When the window's figures were last recomputed; null when no day has been computed yet. */
  computed_at: string | null;
  /**
   * The window's user traces the figures don't include yet (started after their day's last
   * recompute), capped at 10,000. Null only when the window has neither figures nor traces.
   */
  pending_traces: number | null;
}

export interface UserDetail {
  user: UserStats;
  daily: UserDayStats[];
  recent_sessions: SessionSummary[];
  window: UserStatsWindow;
  /** When the user's figures were last recomputed; null when no day has been computed yet. */
  computed_at: string | null;
}

export interface UserListQuery {
  from?: string | undefined;
  to?: string | undefined;
  /** Without it every environment is summed; `""` names the empty environment. */
  environment?: string | undefined;
  sort?: UserSort | undefined;
  /** The server's default is `desc`; users without a value sort last either way. */
  order?: "asc" | "desc" | undefined;
  /** Case-insensitive substring of the user ID (1 to 256 characters). */
  q?: string | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface UserDetailQuery {
  from?: string | undefined;
  to?: string | undefined;
  environment?: string | undefined;
}
