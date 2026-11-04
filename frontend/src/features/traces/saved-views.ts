import type { TraceFilters } from "./search";
import { clearedFilters } from "./use-trace-filters";

/** A built-in view of the trace list: a set of filters and a sort over the current window. */
export interface TraceView {
  id: string;
  label: string;
  filters: Partial<TraceFilters>;
}

export const TRACE_VIEWS: readonly TraceView[] = [
  { id: "all", label: "All", filters: {} },
  { id: "errors", label: "Errors", filters: { status: "error" } },
  { id: "rate-limited", label: "Rate limited", filters: { error_class: "rate_limit" } },
  { id: "slowest", label: "Slowest", filters: { sort: "duration" } },
  { id: "most-expensive", label: "Most expensive", filters: { sort: "cost" } },
  { id: "has-retries", label: "Has retries", filters: { has_retries: true } },
];

/** Every param a view sets or clears. The environment and time range belong to the shell. */
const VIEW_KEYS = [
  ...Object.keys(clearedFilters()).filter((key) => key !== "env"),
  "sort",
  "order",
] as (keyof TraceFilters)[];

/** The URL change that shows a view: its filters, with every other list filter cleared. */
export function viewSearch(view: TraceView): Partial<TraceFilters> {
  return Object.fromEntries(VIEW_KEYS.map((key) => [key, view.filters[key]]));
}

/** The view the URL shows exactly, or null when the filters were changed by hand. */
export function activeViewId(search: TraceFilters): string | null {
  const match = TRACE_VIEWS.find((view) =>
    VIEW_KEYS.every((key) => (search[key] ?? undefined) === view.filters[key]),
  );
  return match?.id ?? null;
}
