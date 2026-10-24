import type { AttributionGroupBy, AttributionResponse, AttributionRow } from "@/lib/api";

import type { DrillContext } from "./drill-down";

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
