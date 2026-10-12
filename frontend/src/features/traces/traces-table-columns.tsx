import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { RowCheckbox } from "./row-checkbox";
import { ROW_GRID, SHOW_SCORES_AND_TAGS, SHOW_TOKENS, SHOW_USER } from "./traces-table-grid";
import type { TraceSelection } from "./use-trace-selection";

export function Cell({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div role="cell" className={className}>
      {children}
    </div>
  );
}

function HeaderCell({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div role="columnheader" className={cn("truncate", className)}>
      {children}
    </div>
  );
}

export function TracesTableHead({ selection }: { selection: TraceSelection | undefined }) {
  return (
    <div role="rowgroup">
      <div
        role="row"
        className={cn(
          ROW_GRID,
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
        <HeaderCell>Started</HeaderCell>
        <HeaderCell className="text-right">Duration</HeaderCell>
        <HeaderCell>Models</HeaderCell>
        <HeaderCell className={cn(SHOW_TOKENS, "text-right")}>Tokens</HeaderCell>
        <HeaderCell className="text-right">Cost</HeaderCell>
        <HeaderCell className={SHOW_SCORES_AND_TAGS}>Scores</HeaderCell>
        <HeaderCell className={SHOW_SCORES_AND_TAGS}>Tags</HeaderCell>
        <HeaderCell className={SHOW_USER}>User / session</HeaderCell>
      </div>
    </div>
  );
}

export function TracesTableSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <div aria-hidden className="flex flex-col gap-0.5">
      <div className={cn(ROW_GRID, "h-9 rounded-input bg-surface-muted")} />
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={cn(ROW_GRID, "h-14")}>
          <span />
          <Skeleton className="mx-auto size-2 rounded-full" />
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-3.5 w-28" />
            <Skeleton className="h-3 w-36 max-w-full" />
          </div>
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="ml-auto h-3.5 w-12" />
          <Skeleton className="h-6 w-32 rounded-full" />
          <Skeleton className={cn(SHOW_TOKENS, "ml-auto h-3.5 w-16")} />
          <Skeleton className="ml-auto h-3.5 w-16" />
          <Skeleton className={cn(SHOW_SCORES_AND_TAGS, "h-6 w-28 rounded-full")} />
          <Skeleton className={cn(SHOW_SCORES_AND_TAGS, "h-6 w-12 rounded-full")} />
          <Skeleton className={cn(SHOW_USER, "h-3.5 w-24")} />
        </div>
      ))}
    </div>
  );
}
