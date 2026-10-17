import { toExportFilters } from "@/features/exports";
import { useProjectFilters } from "@/features/shell/project-context";
import type { ExportFilters } from "@/lib/api";
import { RANGE_LABELS } from "@/lib/time-range";

import { useTraceCountQuery } from "./trace-queries";
import { useTraceFilters } from "./use-trace-filters";

/** What an export would contain if it were started now, from the trace list as it is on screen. */
export interface ExportDraft {
  /** The request's `filters`: the list's window, environment and facets. */
  filters: ExportFilters;
  /** "Last 24 hours", or "Custom range". */
  rangeLabel: string;
  /** How many traces match, or null when that is not known. */
  rows: number | null;
  /**
   * List filters an export cannot apply yet (the score filter, "has scores", "has retries", the
   * prompt version, the trace name), as their chips read: the file includes the traces
   * they hide.
   */
  notApplied: string[];
}

/**
 * The export the trace list would produce. The row count is only known when no facet filter is on:
 * the count comes from the window total, which a filter would make too high, so a filtered list
 * says nothing rather than a wrong number (the same rule as the list's own footer).
 */
export function useExportDraft(): ExportDraft {
  const { search, hasFacetFilters, activeFilters } = useTraceFilters();
  const { range, environment } = useProjectFilters();
  const countQuery = useTraceCountQuery();

  return {
    filters: toExportFilters(range, environment, search),
    rangeLabel: RANGE_LABELS[range.value],
    rows: !hasFacetFilters && countQuery.data !== undefined ? countQuery.data : null,
    notApplied: [
      ...activeFilters
        .filter(
          (filter) =>
            filter.key === "score" || filter.key === "has_scores" || filter.key === "has_retries",
        )
        .map((filter) => `${filter.label.toLowerCase()}: ${filter.value}`),
      ...(search.prompt_version ? ["prompt version"] : []),
      ...(search.name ? ["trace name"] : []),
    ],
  };
}
