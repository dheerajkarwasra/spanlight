import { Link } from "@tanstack/react-router";
import { ChevronRight, Flame, MessageSquare } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type SessionListItem } from "@/lib/api";
import { formatTimestamp, pluralize } from "@/lib/format";

import { formatCompactDuration, formatSessionWindow, isCostOutlier } from "./session-format";

export const ABOVE_P99 = "Above the 99th percentile of session cost in this window";

/** The session's cost, with a flame and its tooltip when the cost is an outlier (spec §8). */
export function SessionCost({
  session,
  costP99,
}: {
  session: SessionListItem;
  costP99: string | null;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      {isCostOutlier(session.cost_usd, costP99) ? (
        <TooltipRoot>
          <TooltipTrigger asChild>
            <span tabIndex={0} aria-label={ABOVE_P99} className="inline-flex rounded-xs">
              <Flame aria-hidden className="size-3.5 text-warning" />
            </span>
          </TooltipTrigger>
          <TooltipContent>{ABOVE_P99}</TooltipContent>
        </TooltipRoot>
      ) : null}
      <CostValue
        cost={session.cost_usd}
        hasUnpriced={session.has_unpriced}
        unknownReason="No price for the models used"
      />
    </span>
  );
}

interface SessionTileProps {
  session: SessionListItem;
  costP99: string | null;
}

/**
 * One session as a tile, the list below `md` (the table takes over above). The session ID is the
 * link; it stretches over the whole tile so the tile is clickable while tooltips stay reachable.
 */
export function SessionTile({ session, costP99 }: SessionTileProps) {
  const { orgId, projectId } = useProjectParams();
  const timeWindow = formatSessionWindow(session.first_at, session.last_at);
  const duration = formatCompactDuration(session.duration_ms);
  const meta = [timeWindow, duration].filter(Boolean).join(" · ");

  return (
    <div className="group relative flex items-center gap-3 rounded-tile bg-surface-muted py-3.5 pr-4 pl-4 transition-colors hover:bg-surface-hover sm:gap-6 sm:pr-5">
      <div className="flex min-w-0 flex-1 items-center gap-3.5">
        <span
          aria-hidden
          className="hidden size-10 shrink-0 items-center justify-center rounded-full border border-border bg-surface sm:flex"
        >
          <MessageSquare className="size-[18px] text-foreground" strokeWidth={1.75} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <Link
            to="/$orgId/$projectId/sessions/$sessionId"
            params={{ orgId, projectId, sessionId: toPathId(session.session_id) }}
            title={session.session_id}
            className="truncate font-mono text-code text-foreground outline-none after:absolute after:inset-0 after:rounded-tile after:outline-offset-2 after:content-[''] focus-visible:after:outline-[1.5px] focus-visible:after:outline-ring"
          >
            {session.session_id}
          </Link>
          {meta ? (
            <p
              title={`First trace ${formatTimestamp(session.first_at) ?? ""}`}
              className="truncate text-xs font-medium text-muted-foreground"
            >
              {meta}
            </p>
          ) : null}
          <p className="truncate text-xs font-medium text-muted-foreground tabular">
            {pluralize(session.trace_count, "turn")}
            {session.external_user_id !== null ? ` · ${session.external_user_id}` : null}
          </p>
        </div>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1">
        {/* Above the stretched link so the cost tooltips work; plain text still clicks through. */}
        <p className="pointer-events-none relative z-10 text-right text-sm font-semibold text-foreground [&_[tabindex]]:pointer-events-auto">
          <span className="sr-only">Cost: </span>
          <SessionCost session={session} costP99={costP99} />
        </p>
        <ErrorsBadge count={session.error_count} />
      </div>

      <ChevronRight
        aria-hidden
        className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
      />
    </div>
  );
}

function ErrorsBadge({ count }: { count: number }) {
  if (count === 0) {
    return <Badge>No errors</Badge>;
  }
  return (
    <Badge variant="danger" className="tabular">
      {pluralize(count, "error")}
    </Badge>
  );
}

export function SessionTileSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-tile bg-surface-muted py-3.5 pr-4 pl-4 sm:gap-6 sm:pr-5">
      <div className="flex min-w-0 flex-1 items-center gap-3.5">
        <Skeleton className="hidden size-10 shrink-0 rounded-full sm:block" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-44 max-w-full" />
          <Skeleton className="h-3 w-36 max-w-full" />
        </div>
      </div>
      <Skeleton className="h-4 w-16" />
      <span className="w-4" />
    </div>
  );
}
