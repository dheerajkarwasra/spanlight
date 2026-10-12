import { useEffect } from "react";

import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { UnknownValue } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { useProjectFilters } from "@/features/shell/project-context";
import { formatInteger } from "@/lib/format";
import { rangePhrase } from "@/lib/time-range";

import { BulkSelectionBar } from "./bulk-selection-bar";
import { recallOpenedTrace, rememberTracesSearch } from "./last-traces-search";
import { loadedSummary, tracesNoun } from "./list-summary";
import { useTraceCountQuery, useTraceListQuery } from "./trace-queries";
import { ExportTracesButton } from "./export-trigger";
import { TraceTiles, TraceTilesSkeleton } from "./trace-tiles";
import { TracesEmptyState } from "./traces-empty-state";
import { TracesTable, TracesTableSkeleton } from "./traces-table";
import { TracesToolbar } from "./traces-toolbar";
import { useTraceFilters } from "./use-trace-filters";
import { useTraceSelection } from "./use-trace-selection";

export function TracesPage() {
  const filters = useTraceFilters();
  const tracesQuery = useTraceListQuery(filters.search);
  const countQuery = useTraceCountQuery();
  const { search } = filters;

  useEffect(() => {
    rememberTracesSearch(search);
  }, [search]);

  const traces = tracesQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const selection = useTraceSelection(traces);
  const isRefreshing =
    tracesQuery.isFetching && !tracesQuery.isFetchingNextPage && !tracesQuery.isPending;
  const stale = tracesQuery.isPlaceholderData;
  const selectedTraceId = recallOpenedTrace();

  const summary = loadedSummary({
    loaded: traces.length,
    total: countQuery.data ?? null,
    filtered: filters.hasFacetFilters,
    hasNextPage: tracesQuery.hasNextPage,
  });
  const footerProps = {
    summary,
    hasNextPage: tracesQuery.hasNextPage,
    isFetchingNextPage: tracesQuery.isFetchingNextPage,
    onLoadMore: () => {
      void tracesQuery.fetchNextPage();
    },
  };
  const loadMoreError = tracesQuery.isFetchNextPageError ? (
    <ErrorState
      compact
      error={tracesQuery.error}
      title="Couldn't load more traces"
      onRetry={() => {
        void tracesQuery.fetchNextPage();
      }}
    />
  ) : null;

  function renderBody() {
    if (tracesQuery.isPending) {
      return (
        <>
          <Card className="hidden p-2 md:block">
            <TracesTableSkeleton />
          </Card>
          <div className="md:hidden">
            <TraceTilesSkeleton />
          </div>
        </>
      );
    }
    if (tracesQuery.isError && traces.length === 0) {
      return (
        <Card>
          <ErrorState
            error={tracesQuery.error}
            title="Couldn't load traces"
            onRetry={() => {
              void tracesQuery.refetch();
            }}
          />
        </Card>
      );
    }
    if (traces.length === 0) {
      return (
        <Card>
          <TracesEmptyState
            hasFacetFilters={filters.hasFacetFilters}
            onClearFilters={filters.clearFilters}
          />
        </Card>
      );
    }
    return (
      <>
        <Card className="@container hidden flex-col p-2 md:flex">
          <TracesTable
            traces={traces}
            selectedTraceId={selectedTraceId}
            stale={stale}
            selection={selection}
          />
          {loadMoreError ?? <ListFooter layout="card" {...footerProps} />}
        </Card>
        <div className="flex flex-col gap-4 md:hidden">
          <TraceTiles traces={traces} selectedTraceId={selectedTraceId} stale={stale} />
          {loadMoreError ? (
            <Card>{loadMoreError}</Card>
          ) : (
            <ListFooter layout="stacked" {...footerProps} />
          )}
        </div>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <PageHeader
        title="Traces"
        description={<TraceCountLine countQuery={countQuery} />}
        actions={<ExportTracesButton />}
      />
      <TracesToolbar
        search={filters.search}
        activeFilters={filters.activeFilters}
        isRefreshing={isRefreshing}
        onFilterChange={filters.setFilter}
        onFiltersChange={filters.setFilters}
        onClearFilters={filters.clearFilters}
      />
      {renderBody()}
      <BulkSelectionBar selection={selection} />
    </div>
  );
}

/** "12,904 traces in the last 24 hours": the overview KPI for the same window and environment. */
function TraceCountLine({ countQuery }: { countQuery: ReturnType<typeof useTraceCountQuery> }) {
  const { range } = useProjectFilters();
  const phrase = rangePhrase(range);

  if (countQuery.data === undefined) {
    const reason = countQuery.isError ? "Couldn't load the trace count" : "Counting traces…";
    return (
      <>
        <UnknownValue reason={reason} /> traces {phrase}
      </>
    );
  }
  const count = countQuery.data;
  return (
    <span className="tabular">
      {formatInteger(count) ?? count} {tracesNoun(count)} {phrase}
    </span>
  );
}
