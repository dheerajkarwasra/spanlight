/**
 * Attribution: a project's spend and usage grouped by trace metadata, trace name, user or
 * release, mirroring `backend/app/api/schemas/attribution.py`. Money is a decimal string; an
 * unknown is `null`, never zero.
 */

export type AttributionGroupBy = "metadata" | "trace_name" | "user" | "release";
export type AttributionSort = "cost" | "calls" | "errors" | "retry_cost";
/** `raw`: a window of 24 hours or less, read live. `rollup`: a longer window, promoted keys only. */
export type AttributionSource = "raw" | "rollup";

/** The figures of every trace started in the window, whatever its group. */
export interface AttributionTotals {
  traces: number;
  llm_calls: number;
  errors: number;
  unpriced_calls: number;
  input_tokens: number;
  output_tokens: number;
  /** A lower bound while `unpriced_calls` is above 0; `null` when nothing was priced. */
  cost_usd: string | null;
  retries: number | null;
  retry_cost_usd: string | null;
}

/** The traces without the dimension (`none`) or the values folded past the daily cap (`other`). */
export interface AttributionBucket extends AttributionTotals {
  /** How many values `other` stands for; `null` on `none`. */
  folded_values: number | null;
}

/** One value of the dimension. */
export interface AttributionRow extends AttributionTotals {
  value: string;
  /** Its cost over the window's total, rounded to four places; `null` when either is unknown. */
  share_of_cost: number | null;
}

export interface AttributionResponse {
  group_by: AttributionGroupBy;
  key: string | null;
  /** In UTC; widened to whole UTC days on the rollup path (`to` exclusive). */
  window: { from: string; to: string };
  source: AttributionSource;
  updated_within_minutes: number;
  items: AttributionRow[];
  next_cursor: string | null;
  /** `null` when no trace lacks the dimension. */
  none: AttributionBucket | null;
  /** `null` when nothing was folded; always `null` on the raw path. */
  other: AttributionBucket | null;
  totals: AttributionTotals;
}

export interface AttributionQuery {
  group_by: AttributionGroupBy;
  /** Required with `group_by=metadata`, refused otherwise. */
  key?: string | undefined;
  from?: string | undefined;
  to?: string | undefined;
  environment?: string | undefined;
  /** The server's default is `cost`. */
  sort?: AttributionSort | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}
