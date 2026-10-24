import { getRouteApi } from "@tanstack/react-router";
import { useMemo } from "react";

import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { InsightStatus } from "@/lib/api";
import { useNow } from "@/lib/use-now";

import { useInsightPages } from "./doctor-queries";
import { InsightEmpty } from "./insight-empty";
import { InsightFilterBar } from "./insight-filter-bar";
import { hasActiveFilters, normalizeFilters, searchToQuery } from "./insight-filters";
import { listSummary, loadedText, STATUS_LABELS } from "./insight-format";
import { InsightList, InsightListSkeleton } from "./insight-list";

const doctorRoute = getRouteApi("/_authed/$orgId/$projectId/doctor");

/** The insights list of the selected status, with its filters. */
export function InsightsTab() {
  const search = doctorRoute.useSearch();
  const navigate = doctorRoute.useNavigate();
  const filters = normalizeFilters({
    status: search.status,
    severity: search.severity,
    kind: search.kind,
  });
  const query = useInsightPages(searchToQuery(filters));
  const filtered = hasActiveFilters(filters);

  function setStatus(status: InsightStatus) {
    void navigate({ search: (prev) => ({ ...prev, status }) });
  }

  return (
    <SectionCard
      title={`${STATUS_LABELS[search.status]} insights`}
      description={<Summary query={query} status={search.status} filtered={filtered} />}
      actions={
        <InsightFilterBar
          filters={filters}
          onChange={(patch) => {
            void navigate({ search: (prev) => ({ ...prev, ...patch }) });
          }}
        />
      }
      className="gap-4"
    >
      <Content
        query={query}
        status={search.status}
        filtered={filtered}
        onClearFilters={() => {
          void navigate({
            search: (prev) => ({ ...prev, severity: undefined, kind: undefined }),
          });
        }}
        onViewResolved={() => {
          setStatus("resolved");
        }}
      />
    </SectionCard>
  );
}

type PagesQuery = ReturnType<typeof useInsightPages>;

function Summary({
  query,
  status,
  filtered,
}: {
  query: PagesQuery;
  status: InsightStatus;
  filtered: boolean;
}) {
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items), [query.data]);
  if (query.isPending) {
    return <Skeleton className="mt-1 h-3 w-40" />;
  }
  if (items === undefined) {
    return "Count unavailable";
  }
  if (items.length === 0) {
    return filtered ? "0 match these filters" : `0 ${STATUS_LABELS[status].toLowerCase()} insights`;
  }
  return listSummary(items, status, query.hasNextPage);
}

interface ContentProps {
  query: PagesQuery;
  status: InsightStatus;
  filtered: boolean;
  onClearFilters: () => void;
  onViewResolved: () => void;
}

function Content({ query, status, filtered, onClearFilters, onViewResolved }: ContentProps) {
  const now = useNow();
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items), [query.data]);

  if (query.isPending) {
    return <InsightListSkeleton />;
  }
  // A failed background refresh keeps the loaded list on screen; polling goes on.
  if (items === undefined) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load insights"
        className="rounded-tile bg-surface-muted py-12"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (items.length === 0) {
    return (
      <InsightEmpty
        status={status}
        filtered={filtered}
        onClearFilters={onClearFilters}
        onViewResolved={onViewResolved}
      />
    );
  }
  return (
    <InsightList
      items={items}
      now={now}
      footer={
        <LoadMoreRow
          loaded={items.length}
          hasNextPage={query.hasNextPage}
          loading={query.isFetchingNextPage}
          onLoadMore={() => {
            void query.fetchNextPage();
          }}
        />
      }
    />
  );
}

interface LoadMoreRowProps {
  loaded: number;
  hasNextPage: boolean;
  loading: boolean;
  onLoadMore: () => void;
}

function LoadMoreRow({ loaded, hasNextPage, loading, onLoadMore }: LoadMoreRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 px-1 pt-1">
      <p className="text-xs font-medium text-muted-foreground tabular" aria-live="polite">
        {loadedText(loaded, hasNextPage)}
      </p>
      {hasNextPage ? (
        <Button size="sm" onClick={onLoadMore} loading={loading}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}
