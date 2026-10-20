import { Link } from "@tanstack/react-router";
import { ChevronRight, MessageSquare } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type SessionSummary } from "@/lib/api";
import { formatInteger, formatTimestamp, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatCompactDuration, formatSessionWindow } from "./session-format";
import { spanBetween } from "./session-summary";

/** Column widths shared by the labels row and every tile (Figma "Session/Tile"). */
const TRACES_COLUMN = "w-20";
const COST_COLUMN = "md:w-[84px]";
const ERRORS_COLUMN = "md:w-[92px]";

/** The column labels above the tiles. Decorative: each tile labels its values for screen readers. */
export function SessionColumnLabels() {
  return (
    <div
      aria-hidden
      className="hidden items-center gap-6 pt-3 pr-5 pb-1.5 pl-[70px] text-overline text-subtle-foreground uppercase md:flex"
    >
      <span className="min-w-0 flex-1">Session</span>
      <span className={TRACES_COLUMN}>Traces</span>
      <span className={cn(COST_COLUMN, "text-right")}>Cost</span>
      <span className={cn(ERRORS_COLUMN, "text-right")}>Errors</span>
      <span className="w-4" />
    </div>
  );
}

/**
 * One session as a tile. The session ID is the link; it stretches over the whole tile so the
 * tile is clickable while tooltips inside it stay reachable.
 */
export function SessionTile({ session }: { session: SessionSummary }) {
  const { orgId, projectId } = useProjectParams();
  const timeWindow = formatSessionWindow(session.first_at, session.last_at);
  const duration = formatCompactDuration(spanBetween(session.first_at, session.last_at));
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
          <p className="text-xs font-medium text-muted-foreground tabular md:hidden">
            {pluralize(session.trace_count, "trace")}
          </p>
        </div>
      </div>

      <p
        className={cn(
          TRACES_COLUMN,
          "hidden text-sm font-semibold text-foreground tabular md:block",
        )}
      >
        <span className="sr-only">Traces: </span>
        {formatInteger(session.trace_count)}
      </p>

      <div className="flex shrink-0 flex-col items-end gap-1 md:contents">
        {/* Above the stretched link so the cost tooltips work; plain text still clicks through. */}
        <p
          className={cn(
            "pointer-events-none relative z-10 text-right text-sm font-semibold text-foreground [&_[tabindex]]:pointer-events-auto",
            COST_COLUMN,
          )}
        >
          <span className="sr-only">Cost: </span>
          <CostValue
            cost={session.cost_usd}
            hasUnpriced={session.has_unpriced}
            unknownReason="No price for the models used"
          />
        </p>
        <div className={cn("flex justify-end", ERRORS_COLUMN)}>
          <ErrorsBadge count={session.error_count} />
        </div>
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
      <Skeleton className={cn(TRACES_COLUMN, "hidden h-4 md:block")} />
      <Skeleton className={cn(COST_COLUMN, "h-4 w-16")} />
      <Skeleton className={cn(ERRORS_COLUMN, "hidden h-6 rounded-full md:block")} />
      <span className="w-4" />
    </div>
  );
}
