import { Info, User } from "lucide-react";
import { Fragment, type ReactNode } from "react";

import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { CopyButton } from "@/components/copy-button";
import { CostValue } from "@/components/cost-value";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatDuration, pluralize } from "@/lib/format";

import { formatSessionWindow } from "./session-format";
import { consistentUserId, turnsOutsideEnvironment, type SessionTotals } from "./session-summary";

/** "Sessions › <session ID>": back to the list. */
export function SessionsBreadcrumbs({ sessionId }: { sessionId: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={sessionId}>
      <Crumb to="/$orgId/$projectId/sessions" params={{ orgId, projectId }}>
        Sessions
      </Crumb>
    </Breadcrumbs>
  );
}

interface SessionHeaderProps {
  sessionId: string;
  traces: TraceSummary[];
  totals: SessionTotals;
  /** Older turns exist that aren't loaded yet, so totals cover loaded turns only. */
  partial: boolean;
}

/**
 * Breadcrumbs, the title with the session ID chip, a one-line summary, and, when an environment is
 * selected and some turns are from another one, a note saying the session shows them all.
 */
export function SessionHeader({ sessionId, traces, totals, partial }: SessionHeaderProps) {
  const userId = consistentUserId(traces);
  const { environment } = useProjectFilters();
  const outside = turnsOutsideEnvironment(traces, environment);

  return (
    <header className="flex flex-col gap-3.5">
      <SessionsBreadcrumbs sessionId={sessionId} />
      <div className="flex min-w-0 flex-wrap items-center gap-3.5">
        <h1 className="text-h1 text-foreground">Session</h1>
        <div className="flex max-w-full min-w-0 items-center gap-1 rounded-full border border-border bg-surface py-1 pr-1.5 pl-3.5">
          <code title={sessionId} className="min-w-0 truncate font-mono text-code text-foreground">
            {sessionId}
          </code>
          <CopyButton
            value={sessionId}
            label="Copy session ID"
            className="size-7 [&_svg]:size-3.5"
          />
        </div>
      </div>
      <SessionStatsLine userId={userId} totals={totals} partial={partial} />
      {environment !== undefined && outside > 0 ? (
        <EnvironmentNote
          environment={environment}
          outside={outside}
          turns={totals.turns}
          partial={partial}
        />
      ) : null}
    </header>
  );
}

interface SessionStatsLineProps {
  userId: string | null;
  totals: SessionTotals;
  partial: boolean;
}

/** "12 turns · $0.214 · 1 error · Today 09:12 → 09:41 · 29m 33s", led by the user chip. */
function SessionStatsLine({ userId, totals, partial }: SessionStatsLineProps) {
  const timeWindow =
    totals.firstAt !== null && totals.lastAt !== null
      ? formatSessionWindow(totals.firstAt, totals.lastAt)
      : null;
  const duration = formatDuration(totals.durationMs);
  const items: { key: string; node: ReactNode }[] = [
    {
      key: "turns",
      node: (
        <span className="font-medium text-foreground tabular">
          {pluralize(totals.turns, "turn")}
          {partial ? "+" : ""}
        </span>
      ),
    },
    {
      key: "cost",
      node: (
        <span className="font-medium text-foreground">
          <span className="sr-only">Total cost: </span>
          <CostValue
            cost={totals.costUsd}
            hasUnpriced={totals.costIsLowerBound}
            unknownReason="No price for the models used"
          />
        </span>
      ),
    },
    {
      key: "errors",
      node:
        totals.errors > 0 ? (
          <span className="font-medium text-danger-text tabular">
            {pluralize(totals.errors, "error")}
          </span>
        ) : (
          <span className="text-muted-foreground">No errors</span>
        ),
    },
  ];
  if (timeWindow !== null) {
    items.push({
      key: "window",
      node: <span className="text-muted-foreground">{timeWindow}</span>,
    });
  }
  if (duration !== null) {
    items.push({
      key: "duration",
      node: <span className="text-muted-foreground tabular">{duration}</span>,
    });
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 text-sm">
      {userId !== null ? <UserChip userId={userId} /> : null}
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        {items.map((item, index) => (
          <Fragment key={item.key}>
            {index > 0 ? (
              <span aria-hidden className="text-subtle-foreground">
                ·
              </span>
            ) : null}
            {item.node}
          </Fragment>
        ))}
      </p>
    </div>
  );
}

interface EnvironmentNoteProps {
  environment: string;
  outside: number;
  turns: number;
  partial: boolean;
}

/** Figma "Session detail — Desktop · Other environments" (413:490), "Environment note". */
function EnvironmentNote({ environment, outside, turns, partial }: EnvironmentNoteProps) {
  return (
    <p className="flex items-start gap-1.5 text-sm text-muted-foreground">
      <Info aria-hidden className="mt-[3px] size-3.5 shrink-0" />
      <span>
        <span className="tabular">
          {outside} of {pluralize(turns, "turn")}
          {partial ? "+" : ""}
        </span>{" "}
        {outside === 1 ? "is" : "are"} outside{" "}
        <span className="font-medium break-all text-foreground">"{environment}"</span>. A session
        always shows all of its turns.
      </span>
    </p>
  );
}

function UserChip({ userId }: { userId: string }) {
  return (
    <span className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-accent-subtle py-1 pr-2.5 pl-1">
      <span
        aria-hidden
        className="flex size-5 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground"
      >
        <User className="size-3" strokeWidth={2.25} />
      </span>
      <span className="sr-only">User: </span>
      <span title={userId} className="min-w-0 truncate font-mono text-label text-accent">
        {userId}
      </span>
    </span>
  );
}

export function SessionHeaderSkeleton() {
  return (
    <div className="flex flex-col gap-3.5" aria-hidden>
      <Skeleton className="h-5 w-20" />
      <div className="flex items-center gap-3.5">
        <Skeleton className="h-9 w-28" />
        <Skeleton className="h-9 w-48 rounded-full" />
      </div>
      <Skeleton className="h-7 w-96 max-w-full rounded-full" />
    </div>
  );
}
