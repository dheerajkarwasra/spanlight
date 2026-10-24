import { useEffect } from "react";

import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { UnknownValue } from "@/components/unknown-value";
import { useTablePrefs, type SortOrder } from "@/components/data-table";
import { Card } from "@/components/ui/card";
import { useProjectFilters, useProjectQuery } from "@/features/shell/project-context";
import { rangePhrase } from "@/lib/time-range";
import { useMediaQuery } from "@/lib/use-media-query";

import { BulkSelectionBar } from "./bulk-selection-bar";
import { recallOpenedTrace, rememberTracesSearch } from "./last-traces-search";
import { loadedSummary, windowCountLine } from "./list-summary";
import { PeekSheet } from "./peek-sheet";
import { TraceHistogram } from "./trace-histogram";
import { useTraceCountQuery, useTraceListQuery } from "./trace-queries";
import { ExportTracesButton } from "./export-trigger";
import { TraceTiles, TraceTilesSkeleton } from "./trace-tiles";
import { TracesEmptyState } from "./traces-empty-state";
import { previewsShown } from "./traces-columns";
import { TracesTable } from "./traces-table";
import { TracesToolbar } from "./traces-toolbar";
import type { TraceFilters } from "./search";
import { onlyRetriesFilter, useTraceFilters } from "./use-trace-filters";
import { usePeek } from "./use-peek";
import { useTraceSelection } from "./use-trace-selection";

const NO_KEYS: readonly string[] = [];
/** Below `md` tiles, from it the table; one renders (2,000 hidden tiles slowed each peek step). */
const PHONE_QUERY = "(max-width: 767px)";

export function TracesPage() {
  const filters = useTraceFilters();
  const phone = useMediaQuery(PHONE_QUERY);
  // The table's column choices (shared with the table): previews only while Input or Output shows.
  const previews = previewsShown(useTablePrefs("traces").overrides);
  const tracesQuery = useTraceListQuery(filters.search, previews);
  const countQuery = useTraceCountQuery();
  const promotedKeys = useProjectQuery().data?.promoted_metadata_keys ?? NO_KEYS;
  const { search } = filters;

  useEffect(() => {
    rememberTracesSearch(search);
  }, [search]);

  const traces = tracesQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const selection = useTraceSelection(traces);
  const peek = usePeek(traces.map((trace) => trace.trace_id));
  const isRefreshing =
    tracesQuery.isFetching && !tracesQuery.isFetchingNextPage && !tracesQuery.isPending;
  const stale = tracesQuery.isPlaceholderData;
  const selectedTraceId = (peek.enabled ? peek.peekId : undefined) ?? recallOpenedTrace();

  const summary = loadedSummary({
    loaded: traces.length,
    total: countQuery.data ?? null,
    filtered: filters.hasFacetFilters,
    hasNextPage: tracesQuery.hasNextPage,
  });
  function setSort(sort: TraceFilters["sort"], order: SortOrder | undefined) {
    filters.setFilters({ sort, order });
  }

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
      return phone ? (
        <TraceTilesSkeleton />
      ) : (
        <Card className="flex flex-col p-2">
          <TracesTable traces={[]} search={search} onSortChange={setSort} loading />
        </Card>
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
            onClearRetries={
              onlyRetriesFilter(filters.activeFilters)
                ? () => {
                    filters.setFilters({ has_retries: undefined });
                  }
                : undefined
            }
          />
        </Card>
      );
    }
    return phone ? (
      <div className="flex flex-col gap-4">
        <TraceTiles
          traces={traces}
          selectedTraceId={selectedTraceId}
          stale={stale}
          promotedKeys={promotedKeys}
          previews={previews}
        />
        {loadMoreError ? (
          <Card>{loadMoreError}</Card>
        ) : (
          <ListFooter layout="stacked" {...footerProps} />
        )}
      </div>
    ) : (
      <Card className="flex flex-col p-2">
        <TracesTable
          traces={traces}
          search={search}
          onSortChange={setSort}
          selectedTraceId={selectedTraceId}
          loading={stale}
          selection={selection}
          onPeek={peek.enabled ? peek.open : undefined}
          footer={loadMoreError ?? <ListFooter layout="card" {...footerProps} />}
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <PageHeader
        title="Traces"
        description={<TraceCountLine countQuery={countQuery} filtered={filters.hasFacetFilters} />}
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
      <TraceHistogram search={search} />
      {renderBody()}
      <BulkSelectionBar selection={selection} />
      {peek.enabled ? <PeekSheet peek={peek} /> : null}
    </div>
  );
}

/** "12,904 traces in the last 24 hours": the overview KPI for the same window and environment. */
function TraceCountLine({
  countQuery,
  filtered,
}: {
  countQuery: ReturnType<typeof useTraceCountQuery>;
  filtered: boolean;
}) {
  const { range } = useProjectFilters();
  const phrase = rangePhrase(range);

  if (countQuery.data === undefined) {
    const reason = countQuery.isError ? "Couldn't load the trace count" : "Counting traces…";
    return (
      <>
        <UnknownValue reason={reason} /> traces {phrase}
        {filtered ? " before filters" : null}
      </>
    );
  }
  return <span className="tabular">{windowCountLine(countQuery.data, phrase, filtered)}</span>;
}
