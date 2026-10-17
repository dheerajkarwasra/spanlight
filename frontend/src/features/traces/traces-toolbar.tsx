import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { HasScoresToggle, ScoreFacet } from "@/features/scores";
import { useFilterOptionsQuery, useProjectQuery } from "@/features/shell/project-context";
import { ERROR_CLASSES, errorClassLabel, isErrorClass } from "@/lib/error-class";
import { cn } from "@/lib/utils";

import { AddFilterPopover } from "./add-filter-popover";
import { AppliedFilterChip, CHIP_BASE, FacetSelect } from "./facet-chips";
import { metadataKeyOptions } from "./metadata-filter";
import { RetryFacets } from "./retry-facets";
import type { TraceFilters } from "./search";
import { TraceSearchInput } from "./trace-search-input";
import {
  removalPatch,
  type ActiveFilter,
  type FilterKey,
  type FilterPatch,
} from "./use-trace-filters";

/** The facet chips' look for the score chips, which live in the scores feature. */
const SCORE_CHIP = cn(
  CHIP_BASE,
  "border border-border-strong bg-surface font-medium text-foreground hover:bg-surface-hover [&_svg]:size-3.5",
);

interface TracesToolbarProps {
  search: TraceFilters;
  activeFilters: ActiveFilter[];
  isRefreshing: boolean;
  onFilterChange: <K extends FilterKey>(key: K, value: TraceFilters[K]) => void;
  onFiltersChange: (patch: FilterPatch) => void;
  onClearFilters: () => void;
}

/**
 * Search, facet chips and the applied-filter row. On phones the facet chips scroll sideways
 * instead of wrapping.
 */
export function TracesToolbar({
  search,
  activeFilters,
  isRefreshing,
  onFilterChange,
  onFiltersChange,
  onClearFilters,
}: TracesToolbarProps) {
  const optionsQuery = useFilterOptionsQuery();
  const options = optionsQuery.data;
  const promotedKeys = useProjectQuery().data?.promoted_metadata_keys ?? [];
  const chips = activeFilters.filter((filter) => filter.key !== "q");

  return (
    <div className="flex flex-col gap-4 md:gap-2.5">
      <div className="flex flex-col gap-4 md:flex-row md:flex-wrap md:items-center md:gap-2">
        <TraceSearchInput
          value={search.q ?? ""}
          onCommit={(value) => {
            onFilterChange("q", value.trim());
          }}
        />
        <div
          role="group"
          aria-label="Filters"
          className="-mx-4 flex [scrollbar-width:none] items-center gap-2 overflow-x-auto px-4 md:mx-0 md:flex-wrap md:overflow-visible md:px-0"
        >
          <FacetSelect
            label="Status"
            value={search.status}
            options={["ok", "error"]}
            optionLabel={(value) => (value === "ok" ? "OK" : "Error")}
            onChange={(value) => {
              onFilterChange("status", value === "ok" || value === "error" ? value : undefined);
            }}
          />
          <FacetSelect
            label="Error class"
            value={search.error_class}
            options={ERROR_CLASSES}
            optionLabel={(value) => (isErrorClass(value) ? errorClassLabel(value) : value)}
            onChange={(value) => {
              onFilterChange(
                "error_class",
                value !== undefined && isErrorClass(value) ? value : undefined,
              );
            }}
          />
          <FacetSelect
            label="Model"
            value={search.model}
            options={options?.models ?? []}
            loading={optionsQuery.isPending}
            onChange={(value) => {
              onFilterChange("model", value);
            }}
          />
          <FacetSelect
            label="Release"
            value={search.release}
            options={options?.releases ?? []}
            loading={optionsQuery.isPending}
            onChange={(value) => {
              onFilterChange("release", value);
            }}
          />
          <FacetSelect
            label="Tag"
            value={search.tag}
            options={options?.tags ?? []}
            loading={optionsQuery.isPending}
            onChange={(value) => {
              onFilterChange("tag", value);
            }}
          />
          <ScoreFacet
            params={search}
            recentNames={options?.score_names ?? []}
            onApply={onFiltersChange}
            chipClassName={SCORE_CHIP}
          />
          <HasScoresToggle
            pressed={search.has_scores === true}
            onChange={(pressed) => {
              onFiltersChange({ has_scores: pressed ? true : undefined });
            }}
            chipClassName={SCORE_CHIP}
          />
          <RetryFacets search={search} onChange={onFiltersChange} />
          <AddFilterPopover
            search={search}
            metadataKeys={metadataKeyOptions(
              promotedKeys,
              options?.metadata_keys ?? [],
              search.metadata_key,
            )}
            onApply={onFiltersChange}
          />
        </div>
        {isRefreshing ? (
          <span
            role="status"
            className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground md:ml-auto"
          >
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
            Updating
          </span>
        ) : null}
      </div>

      {chips.length > 0 ? (
        <ul aria-label="Active filters" className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {chips.map((filter) => (
            <li key={filter.key} className="min-w-0">
              <AppliedFilterChip
                filter={filter}
                onRemove={() => {
                  onFiltersChange(removalPatch(filter.key));
                }}
              />
            </li>
          ))}
          <li>
            <Button
              variant="link"
              size="sm"
              onClick={onClearFilters}
              className="text-sm font-medium"
            >
              Clear all
            </Button>
          </li>
        </ul>
      ) : null}
    </div>
  );
}
