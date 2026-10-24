import { getRouteApi, Link } from "@tanstack/react-router";
import { MessagesSquare } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { fromPathId } from "@/lib/api";

import { SessionActions } from "./session-actions";
import { SessionConversation } from "./session-conversation";
import { SessionHeader, SessionHeaderSkeleton, SessionsBreadcrumbs } from "./session-header";
import { useSessionTurnsQuery } from "./session-queries";
import { sortChronologically, summariseSession } from "./session-summary";
import { SessionSummaryCard } from "./session-summary-card";

const sessionRoute = getRouteApi("/_authed/$orgId/$projectId/sessions/$sessionId");

const PAGE_CLASSES = "flex flex-col gap-6";
/** Conversation column and the 300px Summary card side by side once both fit. */
const BODY_CLASSES = "grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_300px]";

export function SessionDetailPage() {
  const sessionId = fromPathId(sessionRoute.useParams().sessionId);
  const turnsQuery = useSessionTurnsQuery(sessionId);

  if (turnsQuery.isPending) {
    return (
      <div className={PAGE_CLASSES} aria-busy>
        <span className="sr-only" role="status">
          Loading session
        </span>
        <SessionHeaderSkeleton />
        <div className={BODY_CLASSES} aria-hidden>
          <ConversationSkeleton />
          <Skeleton className="h-[720px] rounded-card max-xl:hidden" />
        </div>
      </div>
    );
  }

  if (turnsQuery.isError && turnsQuery.data === undefined) {
    return (
      <div className={PAGE_CLASSES}>
        <SessionsBreadcrumbs sessionId={sessionId} />
        <Card>
          <ErrorState
            error={turnsQuery.error}
            title="Couldn't load this session"
            onRetry={() => {
              void turnsQuery.refetch();
            }}
          />
        </Card>
      </div>
    );
  }

  const turns = sortChronologically(turnsQuery.data.pages.flatMap((page) => page.items));
  const traces = turns.map((turn) => turn.trace);

  if (traces.length === 0) {
    return <SessionNotFound sessionId={sessionId} />;
  }

  const totals = summariseSession(traces);
  const hasEarlierTurns = turnsQuery.hasNextPage;

  return (
    <div className={PAGE_CLASSES}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0 flex-1">
          <SessionHeader
            sessionId={sessionId}
            traces={traces}
            totals={totals}
            partial={hasEarlierTurns}
          />
        </div>
        <SessionActions traces={traces} partial={hasEarlierTurns} />
      </div>
      <div className={BODY_CLASSES}>
        <SessionConversation
          turns={turns}
          hasEarlierTurns={hasEarlierTurns}
          isLoadingEarlier={turnsQuery.isFetchingNextPage}
          loadEarlierFailed={turnsQuery.isFetchNextPageError}
          onLoadEarlier={() => {
            void turnsQuery.fetchNextPage();
          }}
        />
        <SessionSummaryCard
          traces={traces}
          totals={totals}
          partial={hasEarlierTurns}
          className="xl:sticky xl:top-6"
        />
      </div>
    </div>
  );
}

function SessionNotFound({ sessionId }: { sessionId: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className={PAGE_CLASSES}>
      <SessionsBreadcrumbs sessionId={sessionId} />
      <Card>
        <EmptyState
          icon={MessagesSquare}
          title="Session not found"
          description="No traces with this session ID. They may have been deleted by the retention policy, or the ID is wrong."
          action={
            <Button asChild variant="primary">
              <Link to="/$orgId/$projectId/sessions" params={{ orgId, projectId }}>
                Back to sessions
              </Link>
            </Button>
          }
        />
      </Card>
    </div>
  );
}

function ConversationSkeleton() {
  return (
    <Card className="flex flex-col gap-7 p-5 sm:p-7">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-5 w-28" />
          <Skeleton className="h-3.5 w-44" />
        </div>
        <Skeleton className="h-6 w-24 rounded-full" />
      </div>
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="flex flex-col gap-2.5">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="ml-auto h-11 w-1/2 rounded-tile" />
          <Skeleton className="h-16 w-3/4 rounded-tile" />
          <Skeleton className="h-4 w-56" />
        </div>
      ))}
    </Card>
  );
}
