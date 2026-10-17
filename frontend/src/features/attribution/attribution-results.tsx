import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { AttributionSort } from "@/lib/api";

import { footerSentence, noneText } from "./footer-format";
import type { AttributionView } from "./attribution-grid";
import { AttributionSortSheet } from "./attribution-sort-sheet";
import { AttributionTable } from "./attribution-table";
import { AttributionFooterTiles, AttributionTiles } from "./attribution-tiles";

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
  paging: Paging;
}

/** Loaded groups: the table at desktop widths, tiles with a sort sheet below them. */
export function AttributionResults({ view, sort, onSort, paging }: AttributionResultsProps) {
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
    <div className="@container">
      <Card className="hidden flex-col p-2 @4xl:flex">
        <AttributionTable view={view} sort={sort} onSort={onSort} stale={paging.stale} />
        {loadMoreError ?? (
          <ListFooter
            layout="card"
            summary={summary}
            hasNextPage={hasNextPage}
            isFetchingNextPage={paging.isFetchingNextPage}
            onLoadMore={paging.onLoadMore}
          />
        )}
      </Card>
      <div className="flex flex-col gap-4 @4xl:hidden">
        <AttributionSortSheet sort={sort} onSort={onSort} />
        <AttributionTiles view={view} stale={paging.stale} />
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
    </div>
  );
}
