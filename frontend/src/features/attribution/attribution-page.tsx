import { getRouteApi } from "@tanstack/react-router";

import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import {
  useFilterOptionsQuery,
  usePermission,
  useProjectFilters,
  useProjectQuery,
} from "@/features/shell/project-context";
import type { AttributionGroupBy, AttributionSort } from "@/lib/api";

import { AttributionExportButton } from "./attribution-export-dialog";
import { useAttributionQuery } from "./attribution-queries";
import { AttributionResults } from "./attribution-results";
import { withSort } from "./attribution-sort";
import { AttributionEmpty, PickKeyPrompt } from "./attribution-states";
import { AttributionTableHead, AttributionTableSkeleton } from "./attribution-table";
import { AttributionTilesSkeleton } from "./attribution-tiles";
import { drillWindow } from "./drill-down";
import { isFetchDisabled } from "./fetch-rule";
import { GroupByPicker } from "./group-by-picker";
import { NotPromotedNote } from "./not-promoted-note";
import { WindowNote } from "./window-note";

const attributionRoute = getRouteApi("/_authed/$orgId/$projectId/attribution");

const PAGE_SIZE = 50;
const HOUR_MS = 3_600_000;

export function AttributionPage() {
  const search = attributionRoute.useSearch();
  const navigate = attributionRoute.useNavigate();
  const { range, environment } = useProjectFilters();
  const project = useProjectQuery();
  const filters = useFilterOptionsQuery();
  const canPromote = usePermission("project:write");
  const promoted = project.data?.promoted_metadata_keys ?? [];
  const { group_by: groupBy, key, sort } = search;
  const held = isFetchDisabled(groupBy, key, promoted, range.durationMs / HOUR_MS);
  // Until the project loads, a key's promotion is unknown and the note could be wrong.
  const waiting = groupBy === "metadata" && project.isPending;
  const query = useAttributionQuery(
    { group_by: groupBy, key, from: range.from, to: range.to, environment, sort, limit: PAGE_SIZE },
    !held && !waiting,
  );
  // A held query still carries the previous key's or window's page as placeholder data.
  const page = held ? undefined : query.data?.pages[0];

  function onGroupBy(next: AttributionGroupBy, nextKey: string | undefined) {
    void navigate({ search: (previous) => ({ ...previous, group_by: next, key: nextKey }) });
  }
  function onSort(next: AttributionSort) {
    void navigate({ search: (previous) => withSort(previous, next) });
  }
  function loadMore() {
    void query.fetchNextPage();
  }

  function renderBody() {
    if (groupBy === "metadata" && key === undefined) {
      return (
        <Card>
          <PickKeyPrompt />
        </Card>
      );
    }
    if (held && key !== undefined) {
      return <NotPromotedNote metadataKey={key} canPromote={canPromote} />;
    }
    if (waiting || query.isPending) {
      return (
        <div className="@container">
          <Card role="status" aria-label="Loading attribution" className="p-2">
            {/* The same container breakpoint as the loaded table, so the layout does not jump. */}
            <div className="hidden @4xl:block">
              <AttributionTableHead sort={sort} onSort={onSort} />
              <AttributionTableSkeleton />
            </div>
            <div className="@4xl:hidden">
              <AttributionTilesSkeleton />
            </div>
          </Card>
        </div>
      );
    }
    if (query.isError && page === undefined) {
      return (
        <Card>
          <ErrorState
            error={query.error}
            title="Couldn't load attribution"
            description="The request failed. Your group, key and window are kept, so trying again asks for the same table."
            onRetry={() => {
              void query.refetch();
            }}
          />
        </Card>
      );
    }
    if (page === undefined) {
      return null;
    }
    const items = query.data?.pages.flatMap((each) => each.items) ?? [];
    // A metadata key nobody set leaves only "none"; the other kinds still show their none row.
    if (
      items.length === 0 &&
      page.other === null &&
      (page.group_by === "metadata" || page.none === null)
    ) {
      return (
        <Card>
          <AttributionEmpty groupBy={page.group_by} metadataKey={page.key} />
        </Card>
      );
    }
    const view = {
      items,
      page,
      drill: {
        groupBy: page.group_by,
        key: page.key,
        window: drillWindow(page.window, range),
        environment,
      },
    };
    return (
      <AttributionResults
        view={view}
        sort={sort}
        onSort={onSort}
        paging={{
          stale: query.isPlaceholderData,
          hasNextPage: query.hasNextPage,
          isFetchingNextPage: query.isFetchingNextPage,
          loadMoreError: query.isFetchNextPageError ? query.error : null,
          onLoadMore: loadMore,
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <PageHeader
        title="Attribution"
        description={
          <span className="flex flex-col gap-2">
            <span>
              What each customer, feature, release or user cost
              <span className="hidden md:inline">, with calls, errors and retries</span>.
            </span>
            <WindowNote range={range} page={page} />
          </span>
        }
        actions={
          <AttributionExportButton
            view={{ groupBy, key, range, environment, page }}
            disabled={held || waiting}
          />
        }
      />
      <GroupByPicker
        groupBy={groupBy}
        metadataKey={key}
        keys={{
          promoted,
          seen: filters.data?.metadata_keys,
          failed: filters.isError,
          onRetry: () => {
            void filters.refetch();
          },
        }}
        onChange={onGroupBy}
      />
      {renderBody()}
    </div>
  );
}
