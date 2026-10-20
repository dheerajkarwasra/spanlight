import { FileText, Plus } from "lucide-react";
import { useId, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { Dataset, DatasetItem } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { MAX_ITEMS } from "./dataset-errors";
import { useDatasetItemsQuery } from "./dataset-queries";
import { ItemTable, ItemTableSkeleton } from "./item-table";

interface ItemsCardProps {
  dataset: Dataset;
  canWrite: boolean;
  onOpen: (item: DatasetItem, number: number) => void;
  onAddItem: () => void;
  /** The upload button for the empty state. */
  uploadAction: ReactNode;
}

/** Figma "Items" card: the capacity meter, the item table and "Load more". */
export function ItemsCard({ dataset, canWrite, onOpen, onAddItem, uploadAction }: ItemsCardProps) {
  const titleId = useId();
  const query = useDatasetItemsQuery(dataset.id);
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const summary = `Showing ${formatInteger(items.length) ?? ""} of ${pluralize(dataset.item_count, "item")}`;

  function body() {
    if (query.isPending) return <ItemTableSkeleton />;
    if (query.isError && items.length === 0) {
      return (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the items"
          onRetry={() => void query.refetch()}
        />
      );
    }
    if (items.length === 0) {
      return (
        <EmptyState
          icon={FileText}
          title="This dataset has no items"
          description="Add an item by hand, upload a .jsonl or .csv file, or save traces from the Traces page with “Add to dataset”."
          className="rounded-tile bg-surface-muted"
          action={
            canWrite ? (
              <Button variant="primary" onClick={onAddItem}>
                <Plus aria-hidden />
                Add item
              </Button>
            ) : null
          }
        />
      );
    }
    return (
      <>
        <ItemTable items={items} canWrite={canWrite} onOpen={onOpen} />
        {query.isFetchNextPageError ? (
          <ErrorState
            compact
            error={query.error}
            title="Couldn't load more items"
            onRetry={() => void query.fetchNextPage()}
          />
        ) : (
          <ListFooter
            layout="card"
            summary={summary}
            hasNextPage={query.hasNextPage}
            isFetchingNextPage={query.isFetchingNextPage}
            onLoadMore={() => void query.fetchNextPage()}
          />
        )}
      </>
    );
  }

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-col gap-0.5">
          <CardTitle id={titleId}>Items</CardTitle>
          <p className="text-xs font-medium text-muted-foreground">
            {items.length > 0 ? summary : pluralize(dataset.item_count, "item")}
          </p>
        </div>
        {dataset.item_count > 0 ? (
          <CapacityMeter count={dataset.item_count} />
        ) : canWrite ? (
          <div className="flex gap-2">{uploadAction}</div>
        ) : null}
      </div>
      {body()}
    </Card>
  );
}

/** "50 of 10,000 items" over a thin bar; amber once the dataset is full. */
function CapacityMeter({ count }: { count: number }) {
  const full = count >= MAX_ITEMS;
  const ratio = Math.min(1, count / MAX_ITEMS);
  const label = `${formatInteger(count) ?? ""} of ${formatInteger(MAX_ITEMS) ?? ""} items`;
  return (
    <div className="flex w-full flex-col gap-1.5 sm:w-50">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div
        role="meter"
        aria-label="Dataset capacity"
        aria-valuemin={0}
        aria-valuemax={MAX_ITEMS}
        aria-valuenow={count}
        aria-valuetext={label}
        className="h-1.5 overflow-hidden rounded-full bg-surface-muted"
      >
        <div
          className={cn("h-full rounded-full", full ? "bg-warning" : "bg-accent")}
          style={{ width: `${String(Math.max(ratio * 100, count > 0 ? 1 : 0))}%` }}
        />
      </div>
    </div>
  );
}
