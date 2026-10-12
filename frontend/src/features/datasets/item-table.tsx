import { Link } from "@tanstack/react-router";
import { ChevronRight, Link2 } from "lucide-react";
import type { MouseEvent } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import type { DatasetItem } from "@/lib/api";
import { shortId } from "@/lib/format";
import { cn } from "@/lib/utils";

import { metadataChip, valueText } from "./item-values";

const ROW_GRID =
  "grid grid-cols-[32px_minmax(0,1fr)_minmax(0,0.85fr)_160px_120px_32px] items-center gap-4 px-3";
const NOT_SET = "Not set. Add an expected output to score this item.";

interface ItemTableProps {
  items: DatasetItem[];
  onOpen: (item: DatasetItem, number: number) => void;
}

/**
 * Figma "Items" table: number, input, expected output, metadata chips and the source trace. The
 * whole row opens the editor; the chevron is its keyboard path and the source a real link. Below
 * `md` the rows become tiles (Figma "Datasets — Detail — 375").
 */
export function ItemTable({ items, onOpen }: ItemTableProps) {
  function openFromRow(event: MouseEvent<HTMLDivElement>, item: DatasetItem, number: number) {
    if ((event.target as HTMLElement).closest("a, button")) return;
    onOpen(item, number);
  }

  return (
    <>
      <div role="table" aria-label="Items" className="hidden flex-col gap-1.5 md:flex">
        <div
          role="row"
          className={cn(ROW_GRID, "py-2 text-overline text-muted-foreground uppercase")}
        >
          <span role="columnheader">#</span>
          <span role="columnheader">Input</span>
          <span role="columnheader">Expected output</span>
          <span role="columnheader">Metadata</span>
          <span role="columnheader">Source</span>
          <span role="columnheader">
            <span className="sr-only">Edit</span>
          </span>
        </div>
        {items.map((item, index) => (
          <div
            key={item.id}
            role="row"
            onClick={(event) => {
              openFromRow(event, item, index + 1);
            }}
            className={cn(
              ROW_GRID,
              "min-h-15 cursor-pointer rounded-tile bg-surface-muted py-2.5 text-sm transition-colors hover:bg-surface-hover",
            )}
          >
            <span role="cell" className="text-xs text-muted-foreground tabular">
              {index + 1}
            </span>
            <span role="cell" className="line-clamp-2 font-medium text-foreground">
              {valueText(item.input)}
            </span>
            <ExpectedCell value={item.expected_output} />
            <span role="cell" className="flex min-w-0 gap-1 overflow-hidden">
              {Object.entries(item.metadata)
                .slice(0, 3)
                .map(([key, value]) => (
                  <span
                    key={key}
                    className="shrink-0 rounded-full border border-border-strong px-2 py-0.5 font-mono text-2xs text-foreground"
                  >
                    {metadataChip(key, value)}
                  </span>
                ))}
            </span>
            <span role="cell" className="min-w-0">
              <SourceLink item={item} />
            </span>
            <span role="cell">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Edit item ${String(index + 1)}`}
                onClick={() => {
                  onOpen(item, index + 1);
                }}
              >
                <ChevronRight aria-hidden />
              </Button>
            </span>
          </div>
        ))}
      </div>
      <ul className="flex flex-col gap-2 md:hidden">
        {items.map((item, index) => (
          <li key={item.id}>
            <ItemTile item={item} number={index + 1} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </>
  );
}

function ExpectedCell({ value }: { value: unknown }) {
  if (value === null) {
    return (
      <span role="cell" className="line-clamp-2 text-xs text-muted-foreground">
        {NOT_SET}
      </span>
    );
  }
  return (
    <span role="cell" className="line-clamp-2 text-foreground">
      {valueText(value)}
    </span>
  );
}

/** The trace an item came from, or a note that it did not come from one. */
function SourceLink({ item }: { item: DatasetItem }) {
  const { orgId, projectId } = useProjectParams();
  if (item.source_trace_id === null) {
    return <span className="text-xs text-muted-foreground">Not from a trace</span>;
  }
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: item.source_trace_id }}
      search={item.source_span_id ? { span: item.source_span_id } : {}}
      title={`Open trace ${item.source_trace_id}`}
      className="inline-flex max-w-full items-center gap-1.5 rounded-sm font-mono text-label text-accent hover:underline"
    >
      <Link2 aria-hidden className="size-3.5 shrink-0" />
      <span className="truncate">{shortId(item.source_trace_id)}</span>
    </Link>
  );
}

function ItemTile({
  item,
  number,
  onOpen,
}: {
  item: DatasetItem;
  number: number;
  onOpen: ItemTableProps["onOpen"];
}) {
  return (
    <button
      type="button"
      aria-label={`Edit item ${String(number)}`}
      onClick={() => {
        onOpen(item, number);
      }}
      className="flex w-full flex-col gap-1.5 rounded-tile bg-surface-muted p-3.5 text-left transition-colors hover:bg-surface-hover"
    >
      <span className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="tabular">#{number}</span>
        <span className="truncate font-mono">
          {item.source_trace_id ? shortId(item.source_trace_id) : "Not from a trace"}
        </span>
      </span>
      <span className="line-clamp-3 text-sm font-semibold text-foreground">
        {valueText(item.input)}
      </span>
      <span className="line-clamp-2 text-xs text-muted-foreground">
        {item.expected_output === null ? NOT_SET : valueText(item.expected_output)}
      </span>
    </button>
  );
}

export function ItemTableSkeleton() {
  return (
    <div aria-hidden className="flex flex-col gap-1.5">
      {Array.from({ length: 6 }, (_, index) => (
        <div
          key={index}
          className="flex h-15 items-center gap-4 rounded-tile bg-surface-muted px-3"
        >
          <Skeleton className="h-3 w-4 bg-surface" />
          <Skeleton className="h-3.5 w-2/5 bg-surface" />
          <Skeleton className="h-3.5 w-1/3 bg-surface" />
        </div>
      ))}
    </div>
  );
}
