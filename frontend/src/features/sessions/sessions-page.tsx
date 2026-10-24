import { getRouteApi } from "@tanstack/react-router";

import { DataTable, useTablePrefs, type SortOrder } from "@/components/data-table";
import { ErrorState } from "@/components/error-state";
import { FieldSelect } from "@/components/field-select";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { useProjectParams } from "@/features/shell/project-context";
import { useHasTraces } from "@/features/shell/use-has-traces";
import type { SessionListItem, SessionSort } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { useMediaQuery } from "@/lib/use-media-query";

import { useSessionListQuery } from "./session-queries";
import { SessionSearchInput } from "./session-search-input";
import { SessionTile, SessionTileSkeleton } from "./session-tile";
import { sessionColumns, useSessionHref } from "./sessions-columns";
import { SessionsEmptyState } from "./sessions-empty-state";

const sessionsRoute = getRouteApi("/_authed/$orgId/$projectId/sessions");

const SKELETON_TILES = 6;
/** Below `md` the tiles replace the table. */
const PHONE_QUERY = "(max-width: 767px)";

/** The sortable columns with their phone-menu labels, in table order. */
const SORTS: readonly { sort: SessionSort; label: string }[] = [
  { sort: "last", label: "Last turn" },
  { sort: "turns", label: "Turns" },
  { sort: "tokens", label: "Tokens" },
  { sort: "cost", label: "Cost" },
  { sort: "errors", label: "Errors" },
];

function isSessionSort(value: string): value is SessionSort {
  return SORTS.some((option) => option.sort === value);
}

/** Footer wording for the active sort. Unknowns sort last in both directions. */
function sortPhrase(sort: SessionSort, order: SortOrder): string {
  if (sort === "last") {
    return order === "asc" ? "earliest last turn first" : "latest last turn first";
  }
  const direction = order === "asc" ? "lowest first" : "highest first";
  return `sorted by ${sort}, ${direction}${sort === "cost" ? ", unpriced last" : ""}`;
}

export function SessionsPage() {
  const { sort = "last", order = "desc", q } = sessionsRoute.useSearch();
  const navigate = sessionsRoute.useNavigate();
  const { orgId, projectId } = useProjectParams();
  const hasTraces = useHasTraces();
  const sessionHref = useSessionHref();
  // Previews only while the Last turn column shows (spec ruling 4); phone tiles have none.
  const lastTurnShown = useTablePrefs("sessions").overrides.last_turn ?? true;
  const preview = !useMediaQuery(PHONE_QUERY) && lastTurnShown;
  const sessionsQuery = useSessionListQuery({ sort, order, q }, preview);
  const pages = sessionsQuery.data?.pages ?? [];
  const sessions = pages.flatMap((page) => page.items);
  // Only the first page carries the percentile.
  const costP99 = pages[0]?.cost_p99_usd ?? null;
  const stale = sessionsQuery.isPlaceholderData;
  const pending = sessionsQuery.isPending;

  function onSort(next: SessionSort, nextOrder: SortOrder = "desc") {
    void navigate({
      search: (previous) => ({
        ...previous,
        sort: next === "last" ? undefined : next,
        order: nextOrder === "desc" ? undefined : nextOrder,
      }),
    });
  }
  function loadMore() {
    void sessionsQuery.fetchNextPage();
  }

  const count = formatInteger(sessions.length) ?? String(sessions.length);
  const footerProps = {
    summary: `Showing ${count} ${sessions.length === 1 ? "session" : "sessions"} · ${sortPhrase(sort, order)}`,
    // Stale rows (a sort or search just changed) would fetch a page of the new key.
    hasNextPage: sessionsQuery.hasNextPage && !stale,
    isFetchingNextPage: sessionsQuery.isFetchingNextPage,
    onLoadMore: loadMore,
  };
  const loadMoreError = sessionsQuery.isFetchNextPageError ? (
    <ErrorState
      compact
      error={sessionsQuery.error}
      title="Couldn't load more sessions"
      onRetry={loadMore}
    />
  ) : null;

  function renderBody() {
    if (sessionsQuery.isError && sessions.length === 0) {
      return (
        <Card>
          <ErrorState
            error={sessionsQuery.error}
            title="Couldn't load sessions"
            onRetry={() => {
              void sessionsQuery.refetch();
            }}
          />
        </Card>
      );
    }
    if (!pending && sessions.length === 0) {
      return (
        <Card>
          <SessionsEmptyState q={q} unconfigured={hasTraces === false} />
        </Card>
      );
    }
    return (
      <>
        <Card className="hidden flex-col p-2 md:flex">
          <DataTable<SessionListItem>
            id="sessions"
            label="Sessions"
            columns={sessionColumns({ orgId, projectId, costP99, previewsPending: stale })}
            rows={sessions}
            rowKey={(session) => session.session_id}
            rowHref={sessionHref}
            sort={{
              key: sort,
              order,
              onChange: (key, nextOrder) => {
                if (isSessionSort(key)) onSort(key, nextOrder);
              },
            }}
            empty={null}
            loading={pending || stale}
          />
          {pending ? null : (loadMoreError ?? <ListFooter layout="card" {...footerProps} />)}
        </Card>
        <div className="flex flex-col gap-4 md:hidden">
          {pending ? (
            <div role="status" aria-label="Loading sessions" className="flex flex-col gap-1.5">
              {Array.from({ length: SKELETON_TILES }, (_, index) => (
                <SessionTileSkeleton key={index} />
              ))}
            </div>
          ) : (
            <>
              <ul
                aria-label="Sessions"
                aria-busy={stale || undefined}
                className="flex flex-col gap-1.5 transition-opacity aria-busy:opacity-60"
              >
                {sessions.map((session) => (
                  <li key={session.session_id}>
                    <SessionTile session={session} costP99={costP99} />
                  </li>
                ))}
              </ul>
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
      <PageHeader
        title="Sessions"
        description={
          <span className="inline-flex flex-wrap items-center gap-1.5">
            Multi-turn conversations grouped by
            <code className="rounded-xs border border-border bg-surface-muted px-2 py-0.5 font-mono text-label text-foreground">
              session_id
            </code>
          </span>
        }
      />
      <SessionSearchInput />
      <div className="flex flex-col gap-1.5 md:hidden">
        <Label htmlFor="sessions-sort">Sort by</Label>
        <FieldSelect
          id="sessions-sort"
          value={sort}
          onChange={(value) => {
            if (isSessionSort(value)) onSort(value);
          }}
          options={SORTS.map((option) => ({ value: option.sort, label: option.label }))}
        />
      </div>
      {renderBody()}
    </div>
  );
}
