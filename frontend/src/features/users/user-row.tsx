import { Link } from "@tanstack/react-router";
import { ChevronRight, Info, UserRound } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type UserStats } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { lastSeenLabel, lastSeenPhrase } from "./user-format";
import { NO_USER_PRICE, SHOW_CALLS, SHOW_TOKENS, USER_GRID } from "./user-grid";
import { keepWindow } from "./user-sort";

/** The link target for a user; the router URL-encodes the id. */
function UserLink({ userId }: { userId: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/users/$userId"
      params={{ orgId, projectId, userId: toPathId(userId) }}
      search={keepWindow}
      title={userId}
      className="block truncate font-mono text-code font-semibold text-foreground outline-none after:absolute after:inset-0 after:rounded-input after:outline-offset-2 after:content-[''] hover:underline focus-visible:after:outline-[1.5px] focus-visible:after:outline-ring"
    >
      {userId}
    </Link>
  );
}

function UserCost({ user }: { user: UserStats }) {
  return (
    // Above the stretched link so the cost tooltips work; plain text still clicks through.
    <span className="pointer-events-none relative z-10 inline-flex items-center gap-1 [&_[tabindex]]:pointer-events-auto">
      {user.cost_usd === null ? (
        <Info aria-hidden className="size-3.5 text-subtle-foreground" />
      ) : null}
      <CostValue
        cost={user.cost_usd}
        hasUnpriced={user.unpriced_calls > 0}
        unknownReason={NO_USER_PRICE}
      />
    </span>
  );
}

function ErrorCount({ count }: { count: number }) {
  return (
    <span className={cn("tabular", count > 0 && "font-semibold text-danger-text")}>
      {formatInteger(count)}
    </span>
  );
}

/** One user as a table row; the user ID is the link and stretches over the whole row. */
export function UserRow({ user }: { user: UserStats }) {
  return (
    <div
      role="row"
      className={cn(
        USER_GRID,
        "group/row relative h-14 rounded-input text-sm transition-colors hover:bg-surface-hover",
      )}
    >
      <div role="cell" className="flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-accent"
        >
          <UserRound className="size-4" />
        </span>
        <UserLink userId={user.external_user_id} />
      </div>
      <div role="cell" className="text-right tabular">
        {formatInteger(user.traces)}
      </div>
      <div role="cell" className={cn(SHOW_CALLS, "text-right tabular")}>
        {formatInteger(user.llm_calls)}
      </div>
      <div role="cell" className="text-right">
        <ErrorCount count={user.errors} />
      </div>
      <div role="cell" className="text-right font-medium text-foreground">
        <UserCost user={user} />
      </div>
      <div role="cell" className={cn(SHOW_TOKENS, "text-right text-muted-foreground tabular")}>
        {formatInteger(user.tokens)}
      </div>
      <div role="cell" className="truncate text-muted-foreground">
        {lastSeenLabel(user.last_seen_day)}
      </div>
      <ChevronRight
        aria-hidden
        className="size-4 text-muted-foreground transition-transform group-hover/row:translate-x-0.5"
      />
    </div>
  );
}

/** Figma "Users/Mobile tile": ID and cost on top, the counts and last seen below. */
export function UserTile({ user }: { user: UserStats }) {
  return (
    <div className="group relative flex items-center gap-3 rounded-tile bg-surface-muted px-4 py-3.5 transition-colors hover:bg-surface-hover">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline justify-between gap-3">
          <div className="flex min-w-0">
            <UserLink userId={user.external_user_id} />
          </div>
          <span className="shrink-0 text-sm font-semibold text-foreground">
            <UserCost user={user} />
          </span>
        </div>
        <p className="text-xs font-medium text-muted-foreground tabular">
          {pluralize(user.traces, "trace")} · {pluralize(user.llm_calls, "call")} ·{" "}
          <span className={cn(user.errors > 0 && "text-danger-text")}>
            {pluralize(user.errors, "error")}
          </span>{" "}
          · last seen {lastSeenPhrase(user.last_seen_day)}
        </p>
      </div>
      <ChevronRight
        aria-hidden
        className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
      />
    </div>
  );
}

export function UserTileSkeleton() {
  return (
    <div className="flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3.5">
      <div className="flex items-center justify-between gap-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-14" />
      </div>
      <Skeleton className="h-3 w-56 max-w-full" />
    </div>
  );
}

export function UserRowSkeleton() {
  return (
    <div className={cn(USER_GRID, "h-14")}>
      <Skeleton className="h-3.5 w-36 max-w-full" />
      <Skeleton className="ml-auto h-3.5 w-8" />
      <Skeleton className={cn(SHOW_CALLS, "ml-auto h-3.5 w-10")} />
      <Skeleton className="ml-auto h-3.5 w-8" />
      <Skeleton className="ml-auto h-3.5 w-14" />
      <Skeleton className={cn(SHOW_TOKENS, "ml-auto h-3.5 w-14")} />
      <Skeleton className="h-3.5 w-16" />
      <span />
    </div>
  );
}
