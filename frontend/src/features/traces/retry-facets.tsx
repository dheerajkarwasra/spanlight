import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { CHIP_BASE, FacetSelect } from "./facet-chips";
import type { TraceFilters } from "./search";
import type { FilterPatch } from "./use-trace-filters";

const HAS_RETRIES = "has";
const NEWEST = "newest";
const RETRY_COST = "retry_cost_usd";

/** The visible names; the trigger's accessible name repeats the current one ("Sort: Newest"). */
const SORT_LABELS = { [NEWEST]: "Newest", [RETRY_COST]: "Retry cost ↓" } as const;

interface RetryFacetsProps {
  search: Pick<TraceFilters, "has_retries" | "sort">;
  onChange: (patch: FilterPatch) => void;
}

/**
 * Figma "Traces/List/Retry cost column": the "Retries" filter chip (any, or only traces with a
 * retried call) and the "Sort" chip (newest first, or the costliest retries first with unknown
 * cost last). Either one adds the Retry cost column to the table.
 */
export function RetryFacets({ search, onChange }: RetryFacetsProps) {
  const sorted = search.sort === RETRY_COST;
  const sort = sorted ? RETRY_COST : NEWEST;
  return (
    <>
      <FacetSelect
        label="Retries"
        value={search.has_retries === true ? HAS_RETRIES : undefined}
        options={[HAS_RETRIES]}
        optionLabel={() => "Has retries"}
        onChange={(value) => {
          onChange({ has_retries: value === HAS_RETRIES ? true : undefined });
        }}
      />
      <Select
        value={sort}
        onValueChange={(next) => {
          onChange({ sort: next === RETRY_COST ? RETRY_COST : undefined });
        }}
      >
        <SelectTrigger
          aria-label={`Sort: ${SORT_LABELS[sort]}`}
          className={cn(
            CHIP_BASE,
            "w-auto justify-start border-border-strong bg-surface text-foreground [&>svg]:size-3.5 [&>svg]:text-foreground",
            sorted && "border-accent/60",
          )}
        >
          <span className="flex min-w-0 items-center gap-1">
            <span className="shrink-0">Sort:</span>
            <span className="min-w-0 truncate">
              <SelectValue />
            </span>
          </span>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NEWEST}>{SORT_LABELS[NEWEST]}</SelectItem>
          <SelectItem value={RETRY_COST}>{SORT_LABELS[RETRY_COST]}</SelectItem>
        </SelectContent>
      </Select>
    </>
  );
}
