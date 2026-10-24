import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { CHIP_BASE, FacetSelect } from "./facet-chips";
import { sortPatch, TRACE_SORTS, type TraceFilters } from "./search";
import type { FilterPatch } from "./use-trace-filters";

const HAS_RETRIES = "has";

type Sort = (typeof TRACE_SORTS)[number];

/** The visible names; the trigger's accessible name repeats the current one ("Sort: Newest"). */
const DESC_LABELS: Record<Sort, string> = {
  started_at: "Newest",
  duration: "Duration ↓",
  cost: "Cost ↓",
  tokens: "Tokens ↓",
  retry_cost_usd: "Retry cost ↓",
};
/** Ascending: named for what comes first. */
const ASC_LABELS: Record<Sort, string> = {
  started_at: "Oldest",
  duration: "Duration ↑",
  cost: "Cost ↑",
  tokens: "Tokens ↑",
  retry_cost_usd: "Retry cost ↑",
};

/** Every sort in both directions, as "cost:asc", so the phone reaches what the headers can. */
const OPTIONS = TRACE_SORTS.flatMap((sort) => [
  { value: `${sort}:desc`, label: DESC_LABELS[sort] },
  { value: `${sort}:asc`, label: ASC_LABELS[sort] },
]);

interface RetryFacetsProps {
  search: Pick<TraceFilters, "has_retries" | "sort" | "order">;
  onChange: (patch: FilterPatch) => void;
}

/**
 * Figma "Traces/List/Retry cost column": the "Retries" filter chip (any, or only traces with a
 * retried call) and, on phones, the "Sort" chip (largest first, unknown values last); from `md`
 * up the table's headers sort. Retries or the retry-cost sort add the Retry cost column.
 */
export function RetryFacets({ search, onChange }: RetryFacetsProps) {
  const sort = search.sort ?? "started_at";
  const order = search.order ?? "desc";
  const label = (order === "asc" ? ASC_LABELS : DESC_LABELS)[sort];
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
        value={`${sort}:${order}`}
        onValueChange={(next) => {
          onChange(sortPatch(next));
        }}
      >
        <SelectTrigger
          aria-label={`Sort: ${label}`}
          className={cn(
            CHIP_BASE,
            "w-auto justify-start border-border-strong bg-surface text-foreground md:hidden [&>svg]:size-3.5 [&>svg]:text-foreground",
            sort !== "started_at" && "border-accent/60",
          )}
        >
          <span className="flex min-w-0 items-center gap-1">
            <span className="shrink-0">Sort:</span>
            <span className="min-w-0 truncate">
              <SelectValue>{label}</SelectValue>
            </span>
          </span>
        </SelectTrigger>
        <SelectContent>
          {OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </>
  );
}
