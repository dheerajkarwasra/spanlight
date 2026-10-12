import { Link } from "@tanstack/react-router";
import { ArrowUpRight, CircleAlert, RotateCw } from "lucide-react";
import { Fragment, type ReactNode } from "react";

import { CostValue } from "@/components/cost-value";
import { StatusDot } from "@/components/status-dot";
import { ValueOrUnknown } from "@/components/unknown-value";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { useTraceQuery } from "@/features/traces";
import { errorMessage, type TraceDetail, type TraceSummary } from "@/lib/api";
import { formatDuration, formatTimestamp, shortId } from "@/lib/format";

import { FailedBubble, MessageBubble } from "./chat-bubble";
import { earliestFailedSpan, extractTurnConversation } from "./conversation";
import { formatClock } from "./session-format";

interface SessionTurnProps {
  trace: TraceSummary;
  /** 1-based position in the session; null while earlier turns aren't loaded. */
  turnNumber: number | null;
}

/** One trace in a session, read as a user → assistant exchange. */
export function SessionTurn({ trace, turnNumber }: SessionTurnProps) {
  const name = trace.name ?? shortId(trace.trace_id, 12);
  const turnLabel = turnNumber === null ? name : `Turn ${turnNumber}`;

  return (
    <article aria-label={`${turnLabel}: ${name}`} className="flex flex-col gap-2.5">
      <TurnHeader trace={trace} name={name} turnNumber={turnNumber} />
      <TurnExchange trace={trace} name={name} />
      <TurnMeta trace={trace} linkContext={turnNumber === null ? name : `turn ${turnNumber}`} />
    </article>
  );
}

interface TurnHeaderProps {
  trace: TraceSummary;
  name: string;
  turnNumber: number | null;
}

/** Figma "Session/Turn header": status dot, TURN n, trace name, a rule and the start time. */
function TurnHeader({ trace, name, turnNumber }: TurnHeaderProps) {
  const failed = trace.error_count > 0;

  return (
    <div className="flex items-center gap-2.5">
      <h3 className="flex min-w-0 items-center gap-2.5">
        <StatusDot state={failed ? "error" : "ok"} label={failed ? "Failed" : "Succeeded"} />
        {turnNumber !== null ? (
          <span className="shrink-0 text-overline text-muted-foreground uppercase tabular">
            Turn {turnNumber}
          </span>
        ) : null}
        <span className="truncate font-mono text-label font-normal text-subtle-foreground">
          {name}
        </span>
      </h3>
      <span aria-hidden className="h-px min-w-4 flex-1 bg-border" />
      <time
        dateTime={trace.started_at}
        title={formatTimestamp(trace.started_at) ?? undefined}
        className="shrink-0 font-mono text-label text-subtle-foreground tabular"
      >
        {formatClock(trace.started_at, { seconds: true })}
      </time>
    </div>
  );
}

/** The bubbles. Message text lives on the spans, so each turn loads its trace. */
function TurnExchange({ trace, name }: { trace: TraceSummary; name: string }) {
  const traceQuery = useTraceQuery(trace.trace_id);
  const failed = trace.error_count > 0;

  if (traceQuery.isPending) {
    return (
      <div className="flex flex-col gap-2.5" aria-busy>
        <span className="sr-only" role="status">
          Loading conversation
        </span>
        <Skeleton className="ml-auto h-11 w-1/2 rounded-tile" />
        <Skeleton className="h-16 w-3/4 rounded-tile" />
      </div>
    );
  }

  if (traceQuery.isError) {
    return (
      <div className="flex flex-col gap-2.5">
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 rounded-tile bg-surface-muted px-4 py-3 text-xs font-medium text-muted-foreground"
        >
          <CircleAlert aria-hidden className="size-4 shrink-0 text-danger" />
          <span className="min-w-0 flex-1">
            Couldn&apos;t load this turn. {errorMessage(traceQuery.error)}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void traceQuery.refetch();
            }}
          >
            <RotateCw aria-hidden />
            Try again
          </Button>
        </div>
        {failed ? <FailedBubble title={`${name} failed`} message={trace.error_message} /> : null}
      </div>
    );
  }

  return <LoadedExchange trace={trace} detail={traceQuery.data} name={name} />;
}

interface LoadedExchangeProps {
  trace: TraceSummary;
  detail: TraceDetail;
  name: string;
}

function LoadedExchange({ trace, detail, name }: LoadedExchangeProps) {
  const { userText, assistantText } = extractTurnConversation(detail.spans);
  const failed = trace.error_count > 0;
  const failedSpan = failed ? earliestFailedSpan(detail.spans) : null;
  const errorText = trace.error_message ?? failedSpan?.status_message ?? null;

  return (
    <div className="flex flex-col gap-2.5">
      {userText !== null ? <MessageBubble role="user" text={userText} /> : null}
      {assistantText !== null ? <MessageBubble role="assistant" text={assistantText} /> : null}
      {failed ? (
        <FailedBubble title={`${failedSpan?.name ?? name} failed`} message={errorText} />
      ) : null}
      {userText === null && assistantText === null && !failed ? (
        <p className="rounded-tile border border-dashed border-border px-4 py-3 text-xs font-medium text-muted-foreground">
          No chat messages were captured for this turn. Payload capture may be off, or the trace has
          no LLM call.
        </p>
      ) : null}
    </div>
  );
}

/** Figma "Session/Turn meta": model, latency, cost and a link to the full trace. */
function TurnMeta({ trace, linkContext }: { trace: TraceSummary; linkContext: string }) {
  const { orgId, projectId } = useProjectParams();
  const items: { key: string; node: ReactNode }[] = [];

  if (trace.models.length > 0) {
    items.push({
      key: "model",
      node: (
        <span className="font-mono text-label text-muted-foreground">
          <span className="sr-only">Model: </span>
          {trace.models.join(", ")}
        </span>
      ),
    });
  }
  items.push({
    key: "latency",
    node: (
      <span className="tabular">
        <span className="sr-only">Latency: </span>
        <ValueOrUnknown value={formatDuration(trace.duration_ms)} reason="No timing data" />
      </span>
    ),
  });
  items.push({
    key: "cost",
    node: (
      <span className="inline-flex items-center gap-1">
        <span className="sr-only">Cost: </span>
        <CostValue cost={trace.cost_usd} hasUnpriced={trace.has_unpriced} />
      </span>
    ),
  });
  items.push({
    key: "trace",
    node: (
      <Link
        to="/$orgId/$projectId/traces/$traceId"
        params={{ orgId, projectId, traceId: trace.trace_id }}
        className="inline-flex items-center gap-0.5 rounded-xs text-accent hover:underline"
      >
        Open trace
        <span className="sr-only"> for {linkContext}</span>
        <ArrowUpRight aria-hidden className="size-3" />
      </Link>
    ),
  });

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pl-1 text-xs font-medium text-muted-foreground">
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
    </div>
  );
}
