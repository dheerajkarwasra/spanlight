import { Link } from "@tanstack/react-router";
import { ChevronRight, MessagesSquare } from "lucide-react";

import { CostValue } from "@/components/cost-value";
import { StatusDot } from "@/components/status-dot";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { dayLabel, formatClock } from "@/features/sessions/session-format";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type SessionSummary } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

/** The user's most recent sessions (the API returns the last 20 by activity). */
export function UserSessions({ sessions }: { sessions: SessionSummary[] }) {
  return (
    <Card className="flex min-w-0 flex-col gap-3 p-5 sm:p-6">
      <div className="flex flex-col gap-0.5">
        <CardTitle>Recent sessions</CardTitle>
        <CardDescription className="mt-0 font-medium">
          Last 20 by activity · opens the conversation
        </CardDescription>
      </div>
      {sessions.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-2 py-8 text-center">
          <MessagesSquare aria-hidden className="size-6 text-muted-foreground" />
          <p className="max-w-sm text-sm text-muted-foreground">
            No sessions for this user in this window. Sessions appear when traces carry a session
            ID.
          </p>
        </div>
      ) : (
        <>
          <ul aria-label="Recent sessions" className="flex flex-col gap-1.5">
            {sessions.map((session) => (
              <li key={session.session_id}>
                <SessionRow session={session} />
              </li>
            ))}
          </ul>
          <p className="text-xs font-medium text-muted-foreground">
            {sessions.length === 1
              ? "Showing the most recent session"
              : `Showing the ${formatInteger(sessions.length)} most recent sessions`}
          </p>
        </>
      )}
    </Card>
  );
}

/** Figma "Users/Session tile": status dot, ID, then turns · cost · errors · last activity. */
function SessionRow({ session }: { session: SessionSummary }) {
  const { orgId, projectId } = useProjectParams();
  const failed = session.error_count > 0;
  const last = new Date(session.last_at);
  const when = Number.isNaN(last.getTime())
    ? null
    : `${dayLabel(last)} ${formatClock(session.last_at) ?? ""}`.trim();

  return (
    <div className="group relative flex items-center gap-3 rounded-tile bg-surface-muted px-4 py-3 transition-colors hover:bg-surface-hover">
      <StatusDot state={failed ? "error" : "ok"} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Link
          to="/$orgId/$projectId/sessions/$sessionId"
          params={{ orgId, projectId, sessionId: toPathId(session.session_id) }}
          title={session.session_id}
          className="block truncate font-mono text-code text-foreground outline-none after:absolute after:inset-0 after:rounded-tile after:outline-offset-2 after:content-[''] focus-visible:after:outline-2 focus-visible:after:outline-ring"
        >
          {session.session_id}
        </Link>
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs font-medium text-muted-foreground tabular">
          <span>{pluralize(session.trace_count, "turn")}</span>
          <span aria-hidden>·</span>
          {/* Above the stretched link so the cost tooltips work. */}
          <span className="pointer-events-none relative z-10 [&_[tabindex]]:pointer-events-auto">
            <CostValue
              cost={session.cost_usd}
              hasUnpriced={session.has_unpriced}
              unknownReason="No price for the models used"
            />
          </span>
          {failed ? (
            <>
              <span aria-hidden>·</span>
              <span className="text-danger-text">{pluralize(session.error_count, "error")}</span>
            </>
          ) : null}
          {when ? (
            <>
              <span aria-hidden>·</span>
              <span>{when}</span>
            </>
          ) : null}
        </p>
      </div>
      <ChevronRight
        aria-hidden
        className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
      />
    </div>
  );
}

export function UserSessionsSkeleton() {
  return (
    <Card aria-hidden className="flex flex-col gap-2 p-5 sm:p-6">
      <Skeleton className="h-5 w-36" />
      {Array.from({ length: 4 }, (_, index) => (
        <Skeleton key={index} className="h-[56px] rounded-tile" />
      ))}
    </Card>
  );
}
