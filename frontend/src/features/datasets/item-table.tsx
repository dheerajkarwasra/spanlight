import { Link } from "@tanstack/react-router";
import { Link2 } from "lucide-react";

import { DataTable, type DataColumn } from "@/components/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import type { DatasetItem } from "@/lib/api";
import { shortId } from "@/lib/format";

import { metadataChip, valueText } from "./item-values";

const NOT_SET = "Not set. Add an expected output to score this item.";

interface ItemTableProps {
  items: DatasetItem[];
  /** False when the editor opens view-only (no write permission, or the dataset is locked). */
  canWrite: boolean;
  onOpen: (item: DatasetItem, number: number) => void;
}

interface NumberedItem {
  item: DatasetItem;
  number: number;
}

/**
 * Figma "Items" table on the shared `DataTable`: number, input, expected output, metadata chips
 * and the source trace. The whole row opens the editor; the number is its keyboard path and the
 * source a real link. Below `md` the rows become tiles (Figma "Datasets — Detail — 375").
 */
export function ItemTable({ items, canWrite, onOpen }: ItemTableProps) {
  const verb = canWrite ? "Edit" : "Open";
  const rows = items.map((item, index) => ({ item, number: index + 1 }));
  const columns: DataColumn<NumberedItem>[] = [
    {
      id: "number",
      header: "#",
      width: "64px",
      sticky: true,
      cell: ({ item, number }) => (
        <button
          type="button"
          aria-label={`${verb} item ${String(number)}`}
          onClick={() => {
            onOpen(item, number);
          }}
          className="min-h-6 min-w-6 rounded-sm text-xs text-muted-foreground tabular hover:text-foreground"
        >
          {number}
        </button>
      ),
    },
    {
      id: "input",
      header: "Input",
      width: "minmax(200px,1fr)",
      cell: ({ item }) => <span className="truncate font-medium">{valueText(item.input)}</span>,
    },
    {
      id: "expected",
      header: "Expected output",
      width: "minmax(180px,0.85fr)",
      cell: ({ item }) =>
        item.expected_output === null ? (
          <span className="truncate text-xs text-muted-foreground">{NOT_SET}</span>
        ) : (
          <span className="truncate">{valueText(item.expected_output)}</span>
        ),
    },
    {
      id: "metadata",
      header: "Metadata",
      width: "200px",
      hideable: true,
      cell: ({ item }) => (
        <span className="flex min-w-0 gap-1 overflow-hidden">
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
      ),
    },
    {
      id: "source",
      header: "Source",
      width: "130px",
      hideable: true,
      cell: ({ item }) => <SourceLink item={item} />,
    },
  ];

  return (
    <>
      <DataTable<NumberedItem>
        id="dataset-items"
        label="Dataset items"
        columns={columns}
        rows={rows}
        rowKey={({ item }) => item.id}
        onRowClick={({ item, number }) => {
          onOpen(item, number);
        }}
        empty={null}
        loading={false}
      />
      <ul className="flex flex-col gap-2 md:hidden">
        {items.map((item, index) => (
          <li key={item.id}>
            <ItemTile item={item} number={index + 1} verb={verb} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </>
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
  verb,
  onOpen,
}: {
  item: DatasetItem;
  number: number;
  verb: string;
  onOpen: ItemTableProps["onOpen"];
}) {
  return (
    <button
      type="button"
      aria-label={`${verb} item ${String(number)}`}
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
