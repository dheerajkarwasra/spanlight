import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { RowCheckbox } from "./row-checkbox";
import { DEFAULT_LAYOUT, type TableLayout } from "./traces-table-grid";
import type { TraceSelection } from "./use-trace-selection";

export function Cell({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div role="cell" className={className}>
      {children}
    </div>
  );
}

function HeaderCell({
  className,
  sort,
  children,
}: {
  className?: string;
  /** Set on the column the list is sorted by. */
  sort?: "descending";
  children: ReactNode;
}) {
  return (
    <div role="columnheader" aria-sort={sort} className={cn("truncate", className)}>
      {children}
    </div>
  );
}

interface TracesTableHeadProps {
  selection: TraceSelection | undefined;
  layout: TableLayout;
  /** The list is sorted by retry cost: its header carries the arrow. */
  sortedByRetryCost: boolean;
}

export function TracesTableHead({ selection, layout, sortedByRetryCost }: TracesTableHeadProps) {
  return (
    <div role="rowgroup">
      <div
        role="row"
        className={cn(
          layout.row,
          "h-9 rounded-input bg-surface-muted text-overline text-muted-foreground uppercase",
        )}
      >
        <HeaderCell className="flex justify-center overflow-visible">
          {selection ? (
            <RowCheckbox
              checked={selection.state === "some" ? "indeterminate" : selection.state === "all"}
              onCheckedChange={selection.toggleAll}
              label="Select every trace shown"
            />
          ) : (
            <span className="sr-only">Select</span>
          )}
        </HeaderCell>
        <HeaderCell>
          <span className="sr-only">Status</span>
        </HeaderCell>
        <HeaderCell>Name</HeaderCell>
        {layout.metadata !== null ? (
          <HeaderCell className={layout.metadata}>Metadata</HeaderCell>
        ) : null}
        <HeaderCell>Started</HeaderCell>
        <HeaderCell className="text-right">Duration</HeaderCell>
        <HeaderCell className={layout.models}>Models</HeaderCell>
        <HeaderCell className={cn(layout.tokens, "text-right")}>Tokens</HeaderCell>
        <HeaderCell className="text-right">Cost</HeaderCell>
        <HeaderCell className={cn(layout.retries, "text-right")}>Retries</HeaderCell>
        {layout.retryCost ? (
          <HeaderCell className="text-right" sort={sortedByRetryCost ? "descending" : undefined}>
            Retry cost
            {sortedByRetryCost ? <span aria-hidden> ↓</span> : null}
          </HeaderCell>
        ) : null}
        <HeaderCell className={layout.scoresAndTags}>Scores</HeaderCell>
        <HeaderCell className={layout.scoresAndTags}>Tags</HeaderCell>
        <HeaderCell className={layout.user}>User / session</HeaderCell>
      </div>
    </div>
  );
}

export function TracesTableSkeleton({
  rows = 10,
  layout = DEFAULT_LAYOUT,
}: {
  rows?: number;
  layout?: TableLayout;
}) {
  return (
    <div aria-hidden className="flex flex-col gap-0.5">
      <div className={cn(layout.row, "h-9 rounded-input bg-surface-muted")} />
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={cn(layout.row, "h-14")}>
          <span />
          <Skeleton className="mx-auto size-2 rounded-full" />
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-3.5 w-28" />
            <Skeleton className="h-3 w-36 max-w-full" />
          </div>
          {layout.metadata !== null ? (
            <Skeleton className={cn(layout.metadata, "h-6 w-32 rounded-full")} />
          ) : null}
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="ml-auto h-3.5 w-12" />
          <Skeleton className={cn(layout.models, "h-6 w-32 rounded-full")} />
          <Skeleton className={cn(layout.tokens, "ml-auto h-3.5 w-16")} />
          <Skeleton className="ml-auto h-3.5 w-16" />
          <Skeleton className={cn(layout.retries, "ml-auto h-3.5 w-6")} />
          {layout.retryCost ? <Skeleton className="ml-auto h-3.5 w-14" /> : null}
          <Skeleton className={cn(layout.scoresAndTags, "h-6 w-28 rounded-full")} />
          <Skeleton className={cn(layout.scoresAndTags, "h-6 w-12 rounded-full")} />
          <Skeleton className={cn(layout.user, "h-3.5 w-24")} />
        </div>
      ))}
    </div>
  );
}
