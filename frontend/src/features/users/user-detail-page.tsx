import { getRouteApi, Link } from "@tanstack/react-router";
import { ArrowLeft, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { CopyButton } from "@/components/copy-button";
import { CostValue } from "@/components/cost-value";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { fromPathUserId, isApiError, type UserDetail } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { ComputedAtChip } from "./computed-at-chip";
import { UserDailyChart, UserDailyChartSkeleton } from "./user-daily-chart";
import {
  formatUtcDay,
  lastSeenLabel,
  lastSeenPhrase,
  toDailyPoints,
  windowLabel,
} from "./user-format";
import { keepWindow, recallUsersSort } from "./user-sort";
import { NO_USER_PRICE } from "./user-grid";
import { UserSessions, UserSessionsSkeleton } from "./user-sessions";
import { useUserDetailQuery } from "./users-queries";

const userRoute = getRouteApi("/_authed/$orgId/$projectId/users/$userId");

const PAGE_CLASSES = "flex flex-col gap-5";

export function UserDetailPage() {
  const { userId: routeUserId } = userRoute.useParams();
  const userId = fromPathUserId(routeUserId);
  const detailQuery = useUserDetailQuery(userId);

  if (detailQuery.isPending) {
    return (
      <div className={PAGE_CLASSES} aria-busy>
        <span className="sr-only" role="status">
          Loading user
        </span>
        <BackToUsersLink />
        <UserHeaderSkeleton />
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <UserDailyChartSkeleton />
          <UserSessionsSkeleton />
        </div>
      </div>
    );
  }

  if (detailQuery.isError) {
    if (isApiError(detailQuery.error) && detailQuery.error.isNotFound) {
      return (
        <div className={PAGE_CLASSES}>
          <BackToUsersLink />
          <Card>
            <EmptyState
              icon={SearchX}
              title="No activity for this user"
              description="No traces from this user in the selected range. Widen the time range or go back to the list."
            />
          </Card>
        </div>
      );
    }
    return (
      <div className={PAGE_CLASSES}>
        <BackToUsersLink />
        <Card>
          <ErrorState
            error={detailQuery.error}
            title="Couldn't load this user"
            onRetry={() => {
              void detailQuery.refetch();
            }}
          />
        </Card>
      </div>
    );
  }

  const detail = detailQuery.data;
  return (
    <div className={PAGE_CLASSES}>
      <BackToUsersLink />
      <UserHeader detail={detail} userId={userId} />
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <UserDailyChart points={toDailyPoints(detail.daily)} />
        <UserSessions sessions={detail.recent_sessions} />
      </div>
    </div>
  );
}

/** Back to the list, keeping the sort, the time range and the environment. */
function BackToUsersLink() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/users"
      params={{ orgId, projectId }}
      search={(previous) => ({ ...keepWindow(previous), sort: recallUsersSort() })}
      className="inline-flex min-h-6 w-fit items-center gap-1.5 rounded-sm text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft aria-hidden className="size-4" />
      Users
    </Link>
  );
}

/**
 * Figma "Users — Detail" header: the ID, the window line with the computed-at chip, and the ink
 * totals band.
 */
function UserHeader({ detail, userId }: { detail: UserDetail; userId: string }) {
  const { user } = detail;
  return (
    <header className="flex flex-col gap-3">
      <div className="flex min-w-0 items-center gap-3">
        <h1 title={userId} className="min-w-0 text-h1 [overflow-wrap:anywhere] text-foreground">
          {userId}
        </h1>
        <CopyButton value={userId} label="Copy user ID" className="size-7 shrink-0" />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm text-muted-foreground">
          First seen in window: {formatUtcDay(user.first_seen_day)} · last seen{" "}
          {lastSeenPhrase(user.last_seen_day)} · window {windowLabel(detail.window)} (UTC days)
        </p>
        <ComputedAtChip value={detail.computed_at} />
      </div>
      <dl className="grid grid-cols-2 gap-2 rounded-card bg-hero-card p-3 text-hero-card-foreground sm:grid-cols-3 lg:grid-cols-6 dark:border dark:border-border">
        <Stat label="Traces">{formatInteger(user.traces)}</Stat>
        <Stat label="LLM calls">{formatInteger(user.llm_calls)}</Stat>
        <Stat label="Errors">
          <span className={user.errors > 0 ? "text-rail-danger" : undefined}>
            {formatInteger(user.errors)}
          </span>
        </Stat>
        <Stat label="Cost">
          <CostValue
            cost={user.cost_usd}
            hasUnpriced={user.unpriced_calls > 0}
            unknownReason={NO_USER_PRICE}
            unknownTone="on-ink"
          />
        </Stat>
        <Stat label="Tokens">{formatInteger(user.tokens)}</Stat>
        <Stat label="Last seen">{lastSeenLabel(user.last_seen_day)}</Stat>
      </dl>
    </header>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-tile bg-rail-tile px-4 py-3">
      <dt className="text-overline text-rail-muted-foreground uppercase">{label}</dt>
      <dd className="flex min-w-0 items-baseline gap-2 text-card text-xl font-bold whitespace-nowrap tabular">
        {children}
      </dd>
    </div>
  );
}

function UserHeaderSkeleton() {
  return (
    <div aria-hidden className="flex flex-col gap-3">
      <Skeleton className="h-9 w-64 max-w-full" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="grid grid-cols-2 gap-2 rounded-card bg-hero-card p-3 sm:grid-cols-3 lg:grid-cols-6 dark:border dark:border-border">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex flex-col gap-2 rounded-tile bg-rail-tile px-4 py-3">
            <div className="h-3 w-14 rounded-md bg-rail-tile-hover" />
            <div className="h-5 w-20 rounded-md bg-rail-tile-hover" />
          </div>
        ))}
      </div>
    </div>
  );
}
