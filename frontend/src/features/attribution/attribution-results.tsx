import { DataTable } from "@/components/data-table";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { AttributionSort } from "@/lib/api";

import { footerSentence, noneText } from "./footer-format";
import { dimensionName, type AttributionView } from "./attribution-grid";
import { AttributionSortSheet } from "./attribution-sort-sheet";
import { AttributionFooterTiles, AttributionTiles } from "./attribution-tiles";
import { spendColumns } from "./spend-columns";
import { spendRows } from "./spend-rows";

export interface Paging {
  /** Placeholder rows from the previous sort, key or window are on screen. */
  stale: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  loadMoreError: unknown;
  onLoadMore: () => void;
}

interface AttributionResultsProps {
  view: AttributionView;
  sort: AttributionSort;
  onSort: (sort: AttributionSort) => void;
  /** Charts one group (a row's "Show over time"). */
  onFocus: (value: string) => void;
  paging: Paging;
}

/** Loaded groups: the table from `md`, tiles with a sort sheet below it. */
export function AttributionResults({
  view,
  sort,
  onSort,
  onFocus,
  paging,
}: AttributionResultsProps) {
  // Stale rows would page the new query from the old one's cursor.
  const hasNextPage = paging.hasNextPage && !paging.stale;
  const summary = footerSentence({
    loaded: view.items.length,
    sort,
    hasMore: hasNextPage,
    none: view.page.none ? noneText(view.drill) : null,
    hasOther: view.page.other !== null,
  });
  const loadMoreError = paging.loadMoreError ? (
    <ErrorState
      compact
      error={paging.loadMoreError}
      title="Couldn't load more values"
      onRetry={paging.onLoadMore}
    />
  ) : null;

  return (
    <>
      <Card className="hidden p-2 md:block">
        <DataTable
          id="spend"
          label={`Cost by ${dimensionName(view.drill.groupBy, view.drill.key)}`}
          columns={spendColumns({ drill: view.drill, onFocus })}
          rows={spendRows(view)}
          rowKey={(row) => row.key}
          // The API sorts descending only, so a second click asks for the same order.
          sort={{ key: sort, order: "desc", onChange: (key) => onSort(key as AttributionSort) }}
          loading={paging.stale}
          empty={null}
          footer={
            loadMoreError ?? (
              <ListFooter
                layout="card"
                summary={summary}
                hasNextPage={hasNextPage}
                isFetchingNextPage={paging.isFetchingNextPage}
                onLoadMore={paging.onLoadMore}
              />
            )
          }
        />
      </Card>
      <div className="flex flex-col gap-4 md:hidden">
        <AttributionSortSheet sort={sort} onSort={onSort} />
        <AttributionTiles view={view} stale={paging.stale} onFocus={onFocus} />
        {loadMoreError ? <Card>{loadMoreError}</Card> : null}
        {hasNextPage && !loadMoreError ? (
          <Button
            onClick={paging.onLoadMore}
            loading={paging.isFetchingNextPage}
            className="relative w-full before:absolute before:inset-x-0 before:-inset-y-0.5"
          >
            Load more
          </Button>
        ) : null}
        <AttributionFooterTiles view={view} />
      </div>
    </>
  );
}
