import { z } from "zod";

import { ERROR_CLASSES } from "@/lib/error-class";
import { projectSearchSchema, type ProjectSearch } from "@/lib/time-range";

import { isMetadataValueTooLong, METADATA_KEY_PATTERN } from "./metadata-filter";

/** The list's sorts (`started_at` is the server's default) and their direction. */
export const TRACE_SORTS = ["started_at", "duration", "cost", "tokens", "retry_cost_usd"] as const;

/** A prompt version id: a UUID. */
export const promptVersionIdSchema = z.guid();

/** URL-synced facet filters for the traces list. Keys are short to keep URLs readable. */
export const traceFiltersSchema = projectSearchSchema.extend({
  release: z.string().optional().catch(undefined),
  model: z.string().optional().catch(undefined),
  status: z.enum(["ok", "error"]).optional().catch(undefined),
  error_class: z.enum(ERROR_CLASSES).optional().catch(undefined),
  tag: z.string().optional().catch(undefined),
  user: z.string().optional().catch(undefined),
  session: z.string().optional().catch(undefined),
  q: z.string().optional().catch(undefined),
  /** Traces with a score of this name; narrow it with `score_min`/`score_max` or `score_value`. */
  score: z.string().optional().catch(undefined),
  score_min: z.number().optional().catch(undefined),
  score_max: z.number().optional().catch(undefined),
  /** A categorical score's value. */
  score_value: z.string().optional().catch(undefined),
  /** Any score at all. */
  has_scores: z.boolean().optional().catch(undefined),
  /** The prompt version id (a UUID) the trace was produced by; anything else is dropped. */
  prompt_version: promptVersionIdSchema.optional().catch(undefined),
  /** Only traces with at least one retried call. */
  has_retries: z.boolean().optional().catch(undefined),
  /** Traces whose metadata has this key; alone, the traces without it (the "none" drill-down). */
  metadata_key: z.string().regex(METADATA_KEY_PATTERN).optional().catch(undefined),
  /** That key's value, exactly as stored; ignored without `metadata_key`. */
  metadata_value: z
    .string()
    .min(1)
    .refine((value) => !isMetadataValueTooLong(value))
    .optional()
    .catch(undefined),
  /** Traces with exactly this name (`q` is a substring search). */
  name: z.string().optional().catch(undefined),
  /** Absent for newest first (the server's default). */
  sort: z.enum(TRACE_SORTS).optional().catch(undefined),
  /** Absent for descending, so old `sort=retry_cost_usd` links keep their meaning. */
  order: z.enum(["asc", "desc"]).optional().catch(undefined),
  /** The trace open in the peek sheet; not a filter, so the list query leaves it out. */
  peek: z.string().optional().catch(undefined),
});

export type TraceFilters = z.infer<typeof traceFiltersSchema>;

/**
 * The URL change for a phone sort option ("cost:asc"); newest first is the default URL (no sort,
 * no order).
 */
export function sortPatch(option: string): Pick<TraceFilters, "sort" | "order"> {
  const [key, direction] = option.split(":");
  const sort = TRACE_SORTS.find((candidate) => candidate === key);
  const asc = direction === "asc";
  return { sort: sort === "started_at" && !asc ? undefined : sort, order: asc ? "asc" : undefined };
}

/** The two traces of the compare page: `a` is the baseline, `b` the candidate. */
export const traceCompareSearchSchema = projectSearchSchema.extend({
  a: z.string().optional().catch(undefined),
  b: z.string().optional().catch(undefined),
  /** Hide steps with the same output and no change. */
  changed: z.boolean().optional().catch(undefined),
});

/** The compare page's search for two traces, keeping the time range and environment. */
export function compareSearch(prev: ProjectSearch, a: string, b: string) {
  return { range: prev.range, from: prev.from, to: prev.to, env: prev.env, a, b };
}

export const traceDetailSearchSchema = projectSearchSchema.extend({
  span: z.string().optional().catch(undefined),
  /** The inspector tab. Any string is kept (old links say `io`); `resolveTab` picks what shows. */
  tab: z.string().optional().catch(undefined),
});

export type TraceDetailSearch = z.infer<typeof traceDetailSearchSchema>;
