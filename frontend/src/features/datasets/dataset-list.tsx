import { Link } from "@tanstack/react-router";
import { ChevronRight, Database, Info } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { RelativeTime } from "@/components/relative-time";
import { SectionCard } from "@/components/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import type { Dataset } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useDatasetListQuery } from "./dataset-queries";

const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_16px] items-center gap-4 md:grid-cols-[minmax(0,1fr)_100px_130px_16px]";

interface DatasetListProps {
  /** The "New dataset" button for the empty state. */
  createAction: ReactNode;
}

/** Figma "Datasets — List": tile rows linking to each dataset, with loading, empty and error. */
export function DatasetList({ createAction }: DatasetListProps) {
  const query = useDatasetListQuery();
  const datasets = query.data?.pages.flatMap((page) => page.items) ?? [];

  if (query.isPending) {
    return (
      <SectionCard title="Datasets" description="Loading datasets…">
        <DatasetListSkeleton />
      </SectionCard>
    );
  }
  if (query.isError && datasets.length === 0) {
    return (
      <SectionCard title="Datasets">
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load datasets"
          onRetry={() => void query.refetch()}
        />
      </SectionCard>
    );
  }
  if (datasets.length === 0) {
    return (
      <SectionCard title="Datasets" description="0 datasets">
        <EmptyState
          icon={Database}
          title="No datasets yet"
          description="Save traces here, or upload a .jsonl or .csv file, and replay them against any prompt version."
          action={createAction}
          className="rounded-tile bg-surface-muted"
        />
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="Datasets"
      description={listSummary(datasets, query.hasNextPage)}
      className="gap-3"
    >
      <div
        aria-hidden
        className={cn(ROW_GRID, "px-5 py-2 text-overline text-muted-foreground uppercase")}
      >
        <span>Dataset</span>
        <span className="hidden md:block">Items</span>
        <span className="hidden md:block">Updated</span>
        <span />
      </div>
      <ul className="flex flex-col gap-1.5">
        {datasets.map((dataset) => (
          <li key={dataset.id}>
            <DatasetRow dataset={dataset} />
          </li>
        ))}
      </ul>
      {query.isFetchNextPageError ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load more datasets"
          onRetry={() => void query.fetchNextPage()}
        />
      ) : query.hasNextPage ? (
        <ListFooter
          layout="card"
          summary={`Showing ${pluralize(datasets.length, "dataset")}`}
          hasNextPage
          isFetchingNextPage={query.isFetchingNextPage}
          onLoadMore={() => void query.fetchNextPage()}
        />
      ) : null}
      <p className="flex items-center gap-2 px-1 py-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="size-3.5 shrink-0" />A dataset holds up to 10,000 items.
        Uploads accept .jsonl and .csv files up to 5 MB.
      </p>
    </SectionCard>
  );
}

function listSummary(datasets: Dataset[], hasMore: boolean): string {
  if (hasMore) {
    return `Showing ${pluralize(datasets.length, "dataset")}`;
  }
  const items = datasets.reduce((sum, dataset) => sum + dataset.item_count, 0);
  return `${pluralize(datasets.length, "dataset")} · ${pluralize(items, "item")} in total`;
}

function DatasetRow({ dataset }: { dataset: Dataset }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/datasets/$datasetId"
      params={{ orgId, projectId, datasetId: dataset.id }}
      className={cn(
        ROW_GRID,
        "rounded-tile bg-surface-muted px-5 py-3 transition-colors hover:bg-surface-hover",
      )}
    >
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-sm font-semibold text-foreground">{dataset.name}</span>
        {dataset.description ? (
          <span className="truncate text-xs font-medium text-muted-foreground">
            {dataset.description}
          </span>
        ) : null}
        <span className="text-xs font-medium text-muted-foreground md:hidden">
          {pluralize(dataset.item_count, "item")}
        </span>
      </span>
      <span className="hidden font-mono text-label font-medium text-foreground md:block">
        <span className="sr-only">Items: </span>
        {formatInteger(dataset.item_count)}
      </span>
      <span className="hidden text-label font-medium text-muted-foreground md:block">
        <span className="sr-only">Updated </span>
        <RelativeTime iso={dataset.updated_at} />
      </span>
      <ChevronRight aria-hidden className="size-4 text-muted-foreground" />
    </Link>
  );
}

function DatasetListSkeleton() {
  return (
    <div aria-hidden className="flex flex-col gap-1.5">
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} className={cn(ROW_GRID, "rounded-tile bg-surface-muted px-5 py-3.5")}>
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-3.5 w-40 max-w-full bg-surface" />
            <Skeleton className="h-3 w-64 max-w-full bg-surface" />
          </div>
          <Skeleton className="hidden h-3.5 w-10 bg-surface md:block" />
          <Skeleton className="hidden h-3.5 w-20 bg-surface md:block" />
          <span />
        </div>
      ))}
    </div>
  );
}
