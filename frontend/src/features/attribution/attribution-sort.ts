import type { AttributionSort } from "@/lib/api";

import type { AttributionSearch } from "./search";

/** The sortable columns in the sheet's order, with their labels. */
export const SORT_COLUMNS: readonly { sort: AttributionSort; label: string }[] = [
  { sort: "cost", label: "Cost" },
  { sort: "calls", label: "LLM calls" },
  { sort: "errors", label: "Errors" },
  { sort: "retry_cost", label: "Retry cost" },
];

const SORT_PHRASES: Record<AttributionSort, string> = {
  cost: "by cost",
  calls: "by LLM calls",
  errors: "by errors",
  retry_cost: "by retry cost",
};

/** Footer wording for the active sort, as in "Top 8 values by cost". */
export function sortPhrase(sort: AttributionSort): string {
  return SORT_PHRASES[sort];
}

export function sortLabel(sort: AttributionSort): string {
  return SORT_COLUMNS.find((column) => column.sort === sort)?.label ?? "Cost";
}

/** Search params after choosing a sort: the group, key, window and environment are kept. */
export function withSort<T extends AttributionSearch>(search: T, sort: AttributionSort): T {
  return { ...search, sort };
}

/** `aria-sort` of a sortable header: the API always sorts descending, unknowns last. */
export function ariaSortFor(
  column: AttributionSort,
  active: AttributionSort,
): "descending" | "none" {
  return column === active ? "descending" : "none";
}
