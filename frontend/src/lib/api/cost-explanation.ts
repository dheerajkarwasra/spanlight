import { api } from "./client";
import { projectPath } from "./paths";
import type { ServiceTier } from "./telemetry-types";

/**
 * The cost explanation of one span (spec §8.1): the lines behind its stored cost, recomputed from
 * the price row that produced it. Money and rates are decimal strings; unknown is `null`.
 */

/** Why the explanation reads as it does; checked by the server in this precedence. */
export type ExplanationStatus =
  | "ok"
  | "mismatch"
  | "no_usage"
  | "no_price"
  | "price_version_missing"
  | "override_deleted"
  | "cache_hit"
  | "usage_rollup";

export type CostLineKind =
  "uncached_input" | "cache_read" | "cache_write_5m" | "cache_write_1h" | "output" | "web_search";

/** `commitment_billed`: priced at the standard rate, billed under a commitment (Anthropic). */
export type TierNote = "commitment_billed" | "unmodelled";

export interface CostLine {
  kind: CostLineKind;
  quantity: number;
  unit: "tokens" | "requests";
  /** USD per million tokens, or per thousand requests for a fee line. */
  rate: string;
  rate_unit: "usd_per_mtok" | "usd_per_1k_requests";
  /** The tier multiplier; always "1" on a fee line. */
  multiplier: string;
  /** Unrounded; "0" for a zero line. */
  usd: string;
}

/** The price row that produced the stored cost, found by the span's `pricing_version`. */
export interface CostPrice {
  source: "seed" | "override";
  version: string;
  model_pattern: string;
  effective_from: string;
  min_prompt_tokens: number;
  override_id: string | null;
}

export interface CostUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  cache_write_tokens: number | null;
  cache_write_1h_tokens: number | null;
  web_search_requests: number | null;
  reasoning_tokens: number | null;
  /** Why the output count is unknown (`usage.unreported_reason`); null when it is known. */
  unreported_reason: string | null;
}

/** What a gateway cache hit's original call was billed for. */
export interface OriginalUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  cache_write_tokens: number | null;
  cache_write_1h_tokens: number | null;
}

export interface CostExplanation {
  span_id: string;
  trace_id: string;
  status: ExplanationStatus;
  provider: string | null;
  provider_canonical: string | null;
  model: string | null;
  service_tier: ServiceTier | null;
  tier_multiplier: string;
  tier_note: TierNote | null;
  /** Null when the row that priced the span cannot be produced (see `status`). */
  price: CostPrice | null;
  usage: CostUsage;
  lines: CostLine[];
  /** Reasoning tokens are part of `output_tokens`, never a line of their own. */
  output_breakdown: { reasoning: number; other: number } | null;
  recomputed_usd: string | null;
  stored_usd: string | null;
  stored_fee_usd: string | null;
  /** The sentence for every status but `ok`; on `ok`, unpriced searches when there are. */
  message: string | null;
  /** Set on a gateway cache hit only. */
  original_usage: OriginalUsage | null;
}

export const costExplanationApi = {
  /** Each token class and fee of one span as a line, the recomputed total beside the stored one. */
  get: (projectId: string, traceId: string, spanId: string): Promise<CostExplanation> =>
    api.get<CostExplanation>(
      `${projectPath(projectId)}/traces/${encodeURIComponent(traceId)}/spans/${encodeURIComponent(spanId)}/cost-explanation`,
    ),
};
