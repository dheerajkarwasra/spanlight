import { getRouteApi } from "@tanstack/react-router";
import { useEffect } from "react";
import { Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { FieldSelect } from "@/components/field-select";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { useProjectFilters } from "@/features/shell/project-context";
import type { UserSort } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { rangePhrase } from "@/lib/time-range";

import { ComputedAtChip } from "./computed-at-chip";
import { rememberUsersSort, SORT_COLUMNS, sortPhrase, withSort } from "./user-sort";
import { UserTiles, UserTilesSkeleton, UsersTable, UsersTableSkeleton } from "./users-table";
import { useUserListQuery } from "./users-queries";

const usersRoute = getRouteApi("/_authed/$orgId/$projectId/users");

function isUserSort(value: string): value is UserSort {
  return SORT_COLUMNS.some((column) => column.sort === value);
}

export function UsersPage() {
  const { sort } = usersRoute.useSearch();
  const navigate = usersRoute.useNavigate();
  const { range } = useProjectFilters();
  const usersQuery = useUserListQuery(sort);
  const users = usersQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const stale = usersQuery.isPlaceholderData;

  useEffect(() => {
    rememberUsersSort(sort);
  }, [sort]);

  function onSort(next: UserSort) {
    void navigate({ search: (previous) => withSort(previous, next) });
  }
  function loadMore() {
    void usersQuery.fetchNextPage();
  }

  const count = formatInteger(users.length) ?? String(users.length);
  const noun = users.length === 1 ? "user" : "users";
  const footerProps = {
    summary: `Showing ${count} ${noun} · ${sortPhrase(sort)}`,
    // Stale rows (a sort just changed) would fetch a page of the new key from placeholder state.
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
    return (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span>{`${usersQuery.hasNextPage ? "Top " : ""}${count} end ${noun} ${rangePhrase(range)}`}</span>
        <ComputedAtChip value={usersQuery.data?.pages[0]?.computed_at ?? null} />
      </span>
    );
  }

  function renderBody() {
    if (usersQuery.isPending) {
      return (
        <Card role="status" aria-label="Loading users" className="p-2">
          <div className="hidden md:block">
            <UsersTableSkeleton />
          </div>
          <div className="md:hidden">
            <UserTilesSkeleton />
          </div>
        </Card>
      );
    }
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
    if (users.length === 0) {
      return (
        <Card>
          <UsersEmptyState />
        </Card>
      );
    }
    return (
      <>
        <Card className="@container hidden flex-col p-2 md:flex">
          <UsersTable users={users} sort={sort} onSort={onSort} stale={stale} />
          {loadMoreError ?? <ListFooter layout="card" {...footerProps} />}
        </Card>
        <div className="flex flex-col gap-4 md:hidden">
          <UserTiles users={users} stale={stale} />
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
      <PageHeader title="Users" description={headerDescription()} />
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

function UsersEmptyState() {
  return (
    <EmptyState
      icon={Users}
      title="No end users in this window"
      description={
        <p>
          No end users in this window. Set <Code>user_id</Code> on your traces (SDK{" "}
          <Code>user_id=</Code>, OTLP <Code>user.id</Code>, gateway header{" "}
          <Code>x-spanlight-user</Code>).
        </p>
      }
    />
  );
}

function Code({ children }: { children: string }) {
  return (
    <code className="rounded-xs border border-border bg-surface-muted px-1.5 py-0.5 font-mono text-label text-foreground">
      {children}
    </code>
  );
}
