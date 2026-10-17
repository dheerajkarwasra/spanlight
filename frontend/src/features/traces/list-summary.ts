/**
 * Wording for the traces list header and footer. Pure functions only — no React here.
 */
import { formatInteger } from "@/lib/format";

function formatCount(value: number): string {
  return formatInteger(value) ?? String(value);
}

export function tracesNoun(count: number): string {
  return count === 1 ? "trace" : "traces";
}

interface LoadedSummaryInput {
  /** Rows currently on screen. */
  loaded: number;
  /** Traces in the whole time window (overview KPI); null while unknown. */
  total: number | null;
  /** Facet filters narrow the list, so the window total no longer describes it. */
  filtered: boolean;
  hasNextPage: boolean;
}

/** Footer line under the list, e.g. "Showing 50 of 12,904 traces". */
export function loadedSummary({
  loaded,
  total,
  filtered,
  hasNextPage,
}: LoadedSummaryInput): string {
  // The window total only applies to an unfiltered list, and can briefly lag behind new rows.
  if (!filtered && total !== null && total >= loaded) {
    return `Showing ${formatCount(loaded)} of ${formatCount(total)} ${tracesNoun(total)}`;
  }
  const noun = filtered ? `matching ${tracesNoun(loaded)}` : tracesNoun(loaded);
  if (hasNextPage) {
    return `Showing ${formatCount(loaded)} ${noun}`;
  }
  return loaded === 1 ? `1 ${noun}` : `All ${formatCount(loaded)} ${noun} loaded`;
}

/**
 * Header line, e.g. "12,904 traces in the last 24 hours". The count is the window total, so a
 * list narrowed by facet filters says so instead of contradicting its own footer.
 */
export function windowCountLine(count: number, phrase: string, filtered: boolean): string {
  return `${formatCount(count)} ${tracesNoun(count)} ${phrase}${filtered ? " before filters" : ""}`;
}
