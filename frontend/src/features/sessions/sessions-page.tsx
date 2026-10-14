import { MessagesSquare } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useProjectFilters } from "@/features/shell/project-context";
import type { SessionSummary } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { rangePhrase } from "@/lib/time-range";
import { cn } from "@/lib/utils";

import { useSessionListQuery } from "./session-queries";
import { SessionColumnLabels, SessionTile, SessionTileSkeleton } from "./session-tile";

const SKELETON_TILES = 6;

export function SessionsPage() {
  const sessionsQuery = useSessionListQuery();
  const sessions = sessionsQuery.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="flex flex-col gap-6">
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

      <Card className="flex flex-col gap-1.5 p-2">
        {sessionsQuery.isPending ? (
          <SessionListSkeleton />
        ) : sessionsQuery.isError && sessions.length === 0 ? (
          <ErrorState
            error={sessionsQuery.error}
            title="Couldn't load sessions"
            onRetry={() => {
              void sessionsQuery.refetch();
            }}
          />
        ) : sessions.length === 0 ? (
          <SessionsEmptyState />
        ) : (
          <SessionList
            sessions={sessions}
            stale={sessionsQuery.isPlaceholderData}
            hasNextPage={sessionsQuery.hasNextPage}
            isFetchingNextPage={sessionsQuery.isFetchingNextPage}
            fetchNextPageError={sessionsQuery.isFetchNextPageError ? sessionsQuery.error : null}
            onLoadMore={() => {
              void sessionsQuery.fetchNextPage();
            }}
          />
        )}
      </Card>
    </div>
  );
}

interface SessionListProps {
  sessions: SessionSummary[];
  /** The previous range's sessions are shown while the new range loads. */
  stale: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPageError: unknown;
  onLoadMore: () => void;
}

function SessionList({
  sessions,
  stale,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPageError,
  onLoadMore,
}: SessionListProps) {
  const { range } = useProjectFilters();

  return (
    <>
      <SessionColumnLabels />
      <ul
        aria-label="Sessions"
        aria-busy={stale || undefined}
        className={cn("flex flex-col gap-1.5 transition-opacity", stale && "opacity-60")}
      >
        {sessions.map((session) => (
          <li key={session.session_id}>
            <SessionTile session={session} />
          </li>
        ))}
      </ul>

      {fetchNextPageError ? (
        <ErrorState
          compact
          error={fetchNextPageError}
          title="Couldn't load more sessions"
          onRetry={onLoadMore}
        />
      ) : null}

      <div className="flex flex-col items-center gap-3 px-4 pt-2.5 pb-2 text-center">
        {hasNextPage && !fetchNextPageError ? (
          <Button loading={isFetchingNextPage} onClick={onLoadMore}>
            Load more
          </Button>
        ) : null}
        <p className="text-xs font-medium text-muted-foreground" aria-live="polite">
          {hasNextPage ? "Showing the latest " : "Showing "}
          {pluralize(sessions.length, "session")} {rangePhrase(range)}.
        </p>
      </div>
    </>
  );
}

function SessionsEmptyState() {
  const { range, environment } = useProjectFilters();
  return (
    <EmptyState
      icon={MessagesSquare}
      title={environment ? "No sessions in this environment" : "No sessions in this range"}
      description={
        <>
          <p>
            {environment
              ? `Nothing from the "${environment}" environment was grouped into a session ${rangePhrase(range)}. Pick a wider time range or another environment. Sessions appear when traces carry a session ID, for example:`
              : `Nothing was grouped into a session ${rangePhrase(range)}. Sessions appear when traces carry a session ID, for example:`}
          </p>
          <code className="mt-2 inline-block rounded-xs border border-border bg-surface-muted px-2 py-0.5 font-mono text-label text-foreground">
            update_trace(session_id=&quot;chat_123&quot;)
          </code>
        </>
      }
    />
  );
}

function SessionListSkeleton() {
  return (
    <div role="status" aria-label="Loading sessions" className="flex flex-col gap-1.5">
      <SessionColumnLabels />
      {Array.from({ length: SKELETON_TILES }, (_, index) => (
        <SessionTileSkeleton key={index} />
      ))}
    </div>
  );
}
