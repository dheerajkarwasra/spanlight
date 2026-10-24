import { Link } from "@tanstack/react-router";
import { ChevronRight, Info } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type UserStats } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { lastSeenPhrase, NO_USER_PRICE } from "./user-format";
import { keepWindow } from "./user-sort";

/** The link target for a user; the router URL-encodes the id. */
export function UserLink({ userId, stretched }: { userId: string; stretched?: boolean }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/users/$userId"
      params={{ orgId, projectId, userId: toPathId(userId) }}
      search={keepWindow}
      title={userId}
      className={cn(
        "block truncate rounded-sm font-mono text-code font-semibold text-foreground hover:underline",
        // The tile's link covers the whole tile; table rows navigate on click by themselves.
        stretched &&
          "outline-none after:absolute after:inset-0 after:rounded-tile after:outline-offset-2 after:content-[''] focus-visible:after:outline-[1.5px] focus-visible:after:outline-ring",
      )}
    >
      {userId}
    </Link>
  );
}

/** In the tile it sits above the stretched link so the cost tooltips work. */
export function UserCost({ user, inTile }: { user: UserStats; inTile?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1",
        // Plain text still clicks through to the tile's link. Not in the table, where z-10 would
        // paint over the sticky header and User column.
        inTile && "pointer-events-none relative z-10 [&_[tabindex]]:pointer-events-auto",
      )}
    >
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

export function ErrorCount({ count }: { count: number }) {
  return (
    <span className={cn("tabular", count > 0 && "font-semibold text-danger-text")}>
      {formatInteger(count)}
    </span>
  );
}

/** Figma "Users/Mobile tile": ID and cost on top, the counts and last seen below. */
function UserTile({ user }: { user: UserStats }) {
  return (
    <div className="group relative flex items-center gap-3 rounded-tile bg-surface-muted px-4 py-3.5 transition-colors hover:bg-surface-hover">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline justify-between gap-3">
          <div className="flex min-w-0">
            <UserLink userId={user.external_user_id} stretched />
          </div>
          <span className="shrink-0 text-sm font-semibold text-foreground">
            <UserCost user={user} inTile />
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

function UserTileSkeleton() {
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

/** The phone layout: one tile per user. */
export function UserTiles({ users, stale }: { users: UserStats[]; stale: boolean }) {
  return (
    <ul
      aria-label="End users"
      aria-busy={stale || undefined}
      className={cn("flex flex-col gap-1.5 transition-opacity", stale && "opacity-60")}
    >
      {users.map((user) => (
        <li key={user.external_user_id}>
          <UserTile user={user} />
        </li>
      ))}
    </ul>
  );
}

export function UserTilesSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-hidden className="flex flex-col gap-1.5">
      {Array.from({ length: rows }, (_, index) => (
        <UserTileSkeleton key={index} />
      ))}
    </div>
  );
}
