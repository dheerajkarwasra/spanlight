import { Link } from "@tanstack/react-router";
import { ArrowUpRight, Ban, CircleAlert, FlaskConical } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { cn } from "@/lib/utils";

import type { ResultProblem } from "./result-error";
import { ResultMetrics } from "./result-metrics";
import type { RunState } from "./run-state";

const EMPTY_COPY =
  "Pick a prompt version or write messages, choose a credential and model, then run. Each run is a real provider call and shows up as a trace in the playground environment.";

function TraceLink({ traceId }: { traceId: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId }}
      className="inline-flex items-center gap-1 rounded-sm text-xs font-semibold text-accent hover:underline"
    >
      Open trace
      <ArrowUpRight aria-hidden className="size-3" />
    </Link>
  );
}

/** A failed run's tile: warning for a stop the user can lift, danger for a failure. */
export function ProblemTile({ problem }: { problem: ResultProblem }) {
  const warning = problem.tone === "warning";
  const Icon = warning ? Ban : CircleAlert;
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-input p-3",
        warning ? "bg-warning-subtle" : "bg-danger-subtle",
      )}
    >
      <Icon
        aria-hidden
        className={cn("mt-0.5 size-4 shrink-0", warning ? "text-warning" : "text-danger")}
      />
      <div className="flex min-w-0 flex-col gap-1">
        <p
          className={cn("text-label font-semibold", warning ? "text-warning" : "text-danger-text")}
        >
          {problem.title}
        </p>
        <p
          className={cn(
            "text-label [overflow-wrap:anywhere]",
            warning ? "text-foreground" : "text-danger-text",
          )}
        >
          {problem.detail}
        </p>
        {problem.label ? (
          <p className="font-mono text-2xs text-muted-foreground">{problem.label}</p>
        ) : null}
      </div>
    </div>
  );
}

function Running() {
  return (
    <div role="status" className="flex flex-col gap-2.5">
      <span className="sr-only">Running</span>
      <Skeleton className="h-3.5 w-full" />
      <Skeleton className="h-3.5 w-11/12" />
      <Skeleton className="h-3.5 w-3/5" />
    </div>
  );
}

function Empty() {
  return (
    <div className="flex flex-col items-center gap-2 px-2 py-4 text-center">
      <span
        aria-hidden
        className="flex size-12 items-center justify-center rounded-full bg-accent-subtle text-accent-subtle-foreground"
      >
        <FlaskConical className="size-[22px]" strokeWidth={1.75} />
      </span>
      <p className="text-label text-muted-foreground">{EMPTY_COPY}</p>
    </div>
  );
}

function traceIdOf(state: RunState): string | null {
  if (state.status === "done") return state.result.trace_id;
  if (state.status === "failed") return state.traceId;
  return null;
}

/** Figma "Playground — result", "running", the error tiles and the empty state of a column. */
export function ResultCard({ state }: { state: RunState }) {
  const traceId = traceIdOf(state);
  return (
    <section
      aria-label="Output"
      aria-busy={state.status === "running"}
      className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-overline text-subtle-foreground uppercase">Output</h3>
        {traceId ? <TraceLink traceId={traceId} /> : null}
      </div>
      <div aria-live="polite" className="flex flex-col gap-3">
        {state.status === "idle" ? <Empty /> : null}
        {state.status === "running" ? <Running /> : null}
        {state.status === "failed" ? <ProblemTile problem={state.problem} /> : null}
        {state.status === "done" ? (
          <>
            <p className="text-label leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
              {state.result.output}
            </p>
            <ResultMetrics result={state.result} />
          </>
        ) : null}
      </div>
    </section>
  );
}
