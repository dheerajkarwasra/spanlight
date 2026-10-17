import type { AttributionGroupBy, AttributionResponse, AttributionRow } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { DrillContext } from "./drill-down";

/*
 * Figma "Attribution / Page (desktop)": nine columns at 1280 (RULING T20-2). The Value column
 * keeps 160 px and truncates; below the `@4xl` container width the page shows tiles instead.
 *   value · traces · LLM calls · errors · unpriced · cost · share · retries · retry cost
 * Tracks are 764 px; 12 px gaps make 860. At 1280 with the sidebar the row has 866 px, so the
 * gap cannot grow further; at the 896 px breakpoint the tracks use 6 of the row's 12 px end padding.
 */
export const ATTRIBUTION_GRID = cn(
  "grid items-center gap-3 px-3",
  "grid-cols-[minmax(160px,1fr)_64px_76px_64px_72px_80px_104px_60px_84px]",
);

export const NO_GROUP_PRICE = "No priced calls in this group";
export const NO_RETRIES = "No retries";
/** A null retry count: the traces predate retry tracking; it is not zero. */
export const RETRIES_NOT_COMPUTED =
  "Retries are not computed for traces stored before retry tracking was added";
export const RETRY_UNPRICED = "A retried call has no known cost";
export const CAP_REASON =
  "Each UTC day keeps the 1,000 costliest values per key; the rest add up here. Totals stay exact.";
export const REDACTED = "[REDACTED]";

/** The four kinds, in the picker's order, under the names the page and the export dialog use. */
export const GROUP_BY_KINDS = [
  { value: "metadata", label: "Metadata" },
  { value: "trace_name", label: "Trace name" },
  { value: "user", label: "User" },
  { value: "release", label: "Release" },
] as const;

const KIND_NAMES: Record<Exclude<AttributionGroupBy, "metadata">, string> = {
  trace_name: "trace name",
  user: "user",
  release: "release",
};

/** What the rows are grouped by: the metadata key itself, or "trace name", "user", "release". */
export function dimensionName(groupBy: AttributionGroupBy, key: string | null): string {
  return groupBy === "metadata" ? (key ?? "metadata") : KIND_NAMES[groupBy];
}

/** Everything a loaded table or tile list renders from. */
export interface AttributionView {
  /** Every loaded page's values, in order. */
  items: AttributionRow[];
  /** The first page: its `none`, `other` and `totals` cover the whole window. */
  page: AttributionResponse;
  drill: DrillContext;
}
