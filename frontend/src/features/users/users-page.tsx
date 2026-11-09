import { getRouteApi } from "@tanstack/react-router";
import { useEffect } from "react";

import { DataTable, type SortOrder } from "@/components/data-table";
import { ErrorState } from "@/components/error-state";
import { FieldSelect } from "@/components/field-select";
import { SourceHistoryEmpty } from "@/components/freshness-chip";
import { ListFooter } from "@/components/list-footer";
import { LiveIndicator } from "@/components/live-indicator";
import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import { useHasTraces } from "@/features/shell/use-has-traces";
import type { UserSort, UserStats } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { useLiveProject } from "@/lib/live/use-live-project";
import { SOURCE_REMOVED, sourceHistoryGone, sourceHistoryLabel } from "@/lib/freshness";
import { rangePhrase } from "@/lib/time-range";

import { ComputedAtChip } from "./computed-at-chip";
import { UserSearchInput } from "./user-search-input";
import { rememberUsersList, SORT_COLUMNS, sortPhrase, withSort } from "./user-sort";
import { UserTiles, UserTilesSkeleton } from "./user-row";
import { USER_COLUMNS, useUserHref } from "./users-columns";
import { UsersEmptyState } from "./users-empty-state";
import { useUserListQuery } from "./users-queries";

const usersRoute = getRouteApi("/_authed/$orgId/$projectId/users");

function isUserSort(value: string): value is UserSort {
  return SORT_COLUMNS.some((column) => column.sort === value);
}

export function UsersPage() {
  const search = usersRoute.useSearch();
  const { sort = "cost", order = "desc", q } = search;
  const navigate = usersRoute.useNavigate();
  const { range } = useProjectFilters();
  const { projectId } = useProjectParams();
  const { status: liveStatus } = useLiveProject(projectId, range.value !== "custom");
  const hasTraces = useHasTraces();
  const userHref = useUserHref();
  const usersQuery = useUserListQuery({ sort, order, q });
  const users = usersQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const status = usersQuery.data?.pages[0]?.aggregate_status;
  const history = sourceHistoryLabel(status, users.length > 0);
  const stale = usersQuery.isPlaceholderData;

  useEffect(() => {
    // The URL's own values, so "Back to users" keeps a default sort out of the URL too.
    rememberUsersList({ sort: search.sort, order: search.order, q });
  }, [search.sort, search.order, q]);

  function onSort(next: UserSort, nextOrder: SortOrder = "desc") {
    void navigate({ search: (previous) => withSort(previous, next, nextOrder) });
  }
  function loadMore() {
    void usersQuery.fetchNextPage();
  }

  const count = formatInteger(users.length) ?? String(users.length);
  const noun = users.length === 1 ? "user" : "users";
  const footerProps = {
    summary: `Showing ${count} ${noun} · ${sortPhrase(sort, order)}`,
    // Stale rows (a sort or search just changed) would fetch a page of the new key from
    // placeholder state.
    hasNextPage: usersQuery.hasNextPage && !stale,
    isFetchingNextPage: usersQuery.isFetchingNextPage,
    onLoadMore: loadMore,
  };
  const loadMoreError = usersQuery.isFetchNextPageError ? (
    <ErrorState
      compact
      error={usersQuery.error}
      title="Couldn't load more users"
      onRetry={loadMore}
    />
  ) : null;

  function headerDescription() {
    if (usersQuery.isPending) {
      return `End users ${rangePhrase(range)}`;
    }
    if (usersQuery.isError && users.length === 0) {
      return "Count unavailable";
    }
    const first = usersQuery.data?.pages[0];
    const computedAt = first?.computed_at ?? null;
    const pendingTraces = first?.pending_traces ?? null;
    const live = first?.live ?? false;
    return (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {sourceHistoryGone(status, users.length > 0) ? (
          // Nothing retained and no source to count from: the count is unknown, not 0.
          <span>
            <UnknownValue reason={SOURCE_REMOVED} /> end users {rangePhrase(range)}
          </span>
        ) : (
          <span>{`${usersQuery.hasNextPage ? "Top " : ""}${count} end ${noun} ${rangePhrase(range)}`}</span>
        )}
        {/* The figures include every trace so far: they update live. */}
        {live && history === null ? (
          <LiveIndicator status={liveStatus} />
        ) : /* Nothing computed and nothing pending: the window is empty, not behind. */
        !live && computedAt === null && pendingTraces === null && history === null ? null : (
          <ComputedAtChip
            value={computedAt}
            pendingTraces={pendingTraces}
            history={history}
            live={live}
          />
        )}
      </span>
    );
  }

  function renderBody() {
    if (usersQuery.isError && users.length === 0) {
      return (
        <Card>
          <ErrorState
            error={usersQuery.error}
            title="Couldn't load users"
            onRetry={() => {
              void usersQuery.refetch();
            }}
          />
        </Card>
      );
    }
    if (!usersQuery.isPending && users.length === 0) {
      return (
        <Card>
          {history !== null ? (
            <SourceHistoryEmpty />
          ) : (
            <UsersEmptyState q={q} unconfigured={hasTraces === false} />
          )}
        </Card>
      );
    }
    const pending = usersQuery.isPending;
    return (
      <>
        {/* One announcement for both layouts; the skeletons themselves are aria-hidden. */}
        <p className="sr-only" role="status" aria-live="polite">
          {pending ? "Loading users" : ""}
        </p>
        <Card className="hidden flex-col p-2 md:flex">
          <DataTable<UserStats>
            id="users"
            label="End users"
            columns={USER_COLUMNS}
            rows={users}
            rowKey={(user) => user.external_user_id}
            rowHref={userHref}
            sort={{
              key: sort,
              order,
              onChange: (key, nextOrder) => {
                if (isUserSort(key)) onSort(key, nextOrder);
              },
            }}
            empty={null}
            loading={pending || stale}
          />
          {pending ? null : (loadMoreError ?? <ListFooter layout="card" {...footerProps} />)}
        </Card>
        <div className="flex flex-col gap-4 md:hidden">
          {pending ? (
            <UserTilesSkeleton />
          ) : (
            <>
              <UserTiles users={users} stale={stale} />
              {loadMoreError ? (
                <Card>{loadMoreError}</Card>
              ) : (
                <ListFooter layout="stacked" {...footerProps} />
              )}
            </>
          )}
        </div>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <PageHeader title="Users" description={headerDescription()} />
      <UserSearchInput />
      <div className="flex flex-col gap-1.5 md:hidden">
        <Label htmlFor="users-sort">Sort by</Label>
        <FieldSelect
          id="users-sort"
          value={sort}
          onChange={(value) => {
            if (isUserSort(value)) {
              onSort(value);
            }
          }}
          options={SORT_COLUMNS.map((column) => ({ value: column.sort, label: column.label }))}
        />
      </div>
      {renderBody()}
    </div>
  );
}
