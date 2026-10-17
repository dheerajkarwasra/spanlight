import { ArrowDown, ArrowUpDown } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { AttributionSort } from "@/lib/api";
import { cn } from "@/lib/utils";

import { AttributionFooterRows } from "./attribution-footer";
import { ATTRIBUTION_GRID, dimensionName, type AttributionView } from "./attribution-grid";
import { AttributionValueRow } from "./attribution-row";
import { ariaSortFor } from "./attribution-sort";

interface SortProps {
  sort: AttributionSort;
  onSort: (sort: AttributionSort) => void;
}

interface AttributionTableProps extends SortProps {
  view: AttributionView;
  /** Dim rows while results for a new sort, key or window are loading. */
  stale: boolean;
}

/** The desktop table: value rows, then none, other and the total. */
export function AttributionTable({ view, sort, onSort, stale }: AttributionTableProps) {
  return (
    <div
      role="table"
      aria-label={`Cost by ${dimensionName(view.drill.groupBy, view.drill.key)}`}
      aria-busy={stale || undefined}
      className={cn("flex flex-col gap-0.5 transition-opacity", stale && "opacity-60")}
    >
      <AttributionTableHead sort={sort} onSort={onSort} />
      <div role="rowgroup" className="flex flex-col gap-0.5">
        {view.items.map((row) => (
          <AttributionValueRow key={row.value} row={row} drill={view.drill} />
        ))}
      </div>
      <AttributionFooterRows view={view} />
    </div>
  );
}

function PlainHeader({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div role="columnheader" className={cn("truncate", className)}>
      {children}
    </div>
  );
}

function SortHeader({
  column,
  sort,
  onSort,
  children,
}: SortProps & { column: AttributionSort; children: ReactNode }) {
  const isActive = column === sort;
  const Icon = isActive ? ArrowDown : ArrowUpDown;
  return (
    <div role="columnheader" aria-sort={ariaSortFor(column, sort)} className="flex justify-end">
      <button
        type="button"
        onClick={() => {
          onSort(column);
        }}
        className={cn(
          "inline-flex min-h-6 items-center gap-1 rounded-sm whitespace-nowrap uppercase hover:text-foreground",
          isActive && "text-foreground",
        )}
      >
        {children}
        <Icon aria-hidden className="size-3" />
      </button>
    </div>
  );
}

export function AttributionTableHead({ sort, onSort }: SortProps) {
  const sortProps = { sort, onSort };
  return (
    <div role="rowgroup">
      <div
        role="row"
        className={cn(
          ATTRIBUTION_GRID,
          "h-9 rounded-input bg-surface-muted text-overline text-muted-foreground uppercase",
        )}
      >
        <PlainHeader>Value</PlainHeader>
        <PlainHeader className="text-right">Traces</PlainHeader>
        <SortHeader column="calls" {...sortProps}>
          LLM calls
        </SortHeader>
        <SortHeader column="errors" {...sortProps}>
          Errors
        </SortHeader>
        <PlainHeader className="text-right">Unpriced</PlainHeader>
        <SortHeader column="cost" {...sortProps}>
          Cost
        </SortHeader>
        <PlainHeader className="text-right">Share</PlainHeader>
        <PlainHeader className="text-right">Retries</PlainHeader>
        <SortHeader column="retry_cost" {...sortProps}>
          Retry cost
        </SortHeader>
      </div>
    </div>
  );
}

export function AttributionTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-hidden className="flex flex-col gap-0.5">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={cn(ATTRIBUTION_GRID, "h-[52px]")}>
          <Skeleton className="h-3 w-28 max-w-full" />
          {Array.from({ length: 8 }, (_, cell) => (
            <Skeleton key={cell} className="ml-auto h-3 w-10" />
          ))}
        </div>
      ))}
    </div>
  );
}
