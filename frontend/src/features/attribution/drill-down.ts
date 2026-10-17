import type { AttributionGroupBy } from "@/lib/api";
import { MAX_WINDOW_MS } from "@/lib/time-range";

/** What a row's link needs to rebuild its group as a trace list filter. */
export interface DrillContext {
  groupBy: AttributionGroupBy;
  key: string | null;
  /** The response's window, so the list counts the same traces as the row. */
  window: { from: string; to: string };
  environment: string | undefined;
}

interface Window {
  from: string;
  to: string;
}

/**
 * The window a row links with: the response's (UTC days on the rollup path), so the list counts
 * the row's traces, unless widening made it longer than the trace list accepts (90 days); then
 * the window the page asked for.
 */
export function drillWindow(response: Window, requested: Window): Window {
  const span = new Date(response.to).getTime() - new Date(response.from).getTime();
  return span > MAX_WINDOW_MS ? { from: requested.from, to: requested.to } : response;
}

/** Trace list search params; `metadata_*` and `name` are read by the list from Bet 3 Task 25. */
export interface TraceDrillSearch {
  range: "custom";
  from: string;
  to: string;
  env?: string | undefined;
  metadata_key?: string;
  metadata_value?: string;
  name?: string;
  user?: string;
  release?: string;
}

/**
 * The trace list filter for one value, or for `null` (the "none" bucket). Only a metadata key
 * can filter on its absence; the other kinds' none bucket has no list filter, so `null` comes
 * back and the row is not a link.
 */
export function drillDownSearch(
  context: DrillContext,
  value: string | null,
): TraceDrillSearch | null {
  const base: TraceDrillSearch = {
    range: "custom",
    from: context.window.from,
    to: context.window.to,
    env: context.environment,
  };
  if (context.groupBy === "metadata") {
    if (context.key === null) {
      return null;
    }
    return value === null
      ? { ...base, metadata_key: context.key }
      : { ...base, metadata_key: context.key, metadata_value: value };
  }
  if (value === null) {
    return null;
  }
  if (context.groupBy === "trace_name") {
    return { ...base, name: value };
  }
  return context.groupBy === "user" ? { ...base, user: value } : { ...base, release: value };
}
