import { Link } from "@tanstack/react-router";

import { RelativeTime } from "@/components/relative-time";
import { ScoreBadge } from "@/components/score-badge";
import { Badge } from "@/components/ui/badge";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { EvaluatorRun, EvaluatorRunSource } from "@/lib/api";
import { shortId } from "@/lib/format";
import { toScoreParts } from "@/lib/score-format";
import { cn } from "@/lib/utils";

import { BUDGET_SKIPPED_COPY, formatRunCost, runErrorCopy, runState } from "./run-copy";
import { RUN_COLUMNS } from "./run-columns";
import { RunStatus } from "./run-status";

const SOURCE_LABELS: Record<EvaluatorRunSource, string | null> = {
  online: null,
  test: "Test",
  experiment: "Experiment",
};
const SHOWN_SCORES = 3;

/** The scores a done run wrote: the value in bold, then its name (a webhook may write several). */
function RunScores({ run, booleanScores }: { run: EvaluatorRun; booleanScores: boolean }) {
  if (run.scores.length === 0) {
    return <p className="text-xs font-medium text-muted-foreground">No score was written.</p>;
  }
  const extra = run.scores.length - SHOWN_SCORES;
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
      {run.scores.slice(0, SHOWN_SCORES).map((score) => (
        <span key={score.name} className="inline-flex items-baseline gap-1.5">
          <ScoreBadge
            score={toScoreParts(score)}
            dataType={booleanScores ? "boolean" : null}
            size="sm"
          />
          {score.name}
        </span>
      ))}
      {extra > 0 ? <span>+{extra} more</span> : null}
    </p>
  );
}

function RunResult({ run, booleanScores }: { run: EvaluatorRun; booleanScores: boolean }) {
  const state = runState(run);
  if (state === "skipped") {
    return <p className="text-xs font-medium text-muted-foreground">{BUDGET_SKIPPED_COPY}</p>;
  }
  if (state === "failed") {
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-xs font-medium text-danger-text">{runErrorCopy(run.error ?? "")}</p>
        {run.error ? (
          <p className="truncate font-mono text-[11px] text-muted-foreground">{run.error}</p>
        ) : null}
      </div>
    );
  }
  if (state === "done") {
    return <RunScores run={run} booleanScores={booleanScores} />;
  }
  return (
    <p className="text-xs font-medium text-muted-foreground">
      {state === "running" ? "Scoring now." : "Waiting for a worker."}
    </p>
  );
}

function RunCost({ run }: { run: EvaluatorRun }) {
  const state = runState(run);
  if (state === "running" || state === "queued") {
    return <UnknownValue reason="Not settled yet." />;
  }
  const cost = formatRunCost(run.cost_usd);
  return cost === null ? (
    <UnknownValue reason="Unreadable cost." />
  ) : (
    <span className="font-mono text-xs text-foreground">{cost}</span>
  );
}

/** One run: the trace it scored and when, its state, what came of it, and its charge. */
export function RunRow({ run, booleanScores }: { run: EvaluatorRun; booleanScores: boolean }) {
  const { orgId, projectId } = useProjectParams();
  const source = SOURCE_LABELS[run.source];
  return (
    <li className="flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3 @[40rem]:flex-row @[40rem]:items-center @[40rem]:gap-4">
      <div
        className={cn("flex items-center justify-between gap-3 @[40rem]:block", RUN_COLUMNS.trace)}
      >
        <div className="flex flex-col">
          <Link
            to="/$orgId/$projectId/traces/$traceId"
            params={{ orgId, projectId, traceId: run.trace_id }}
            className="w-fit rounded-sm font-mono text-xs font-medium text-accent hover:underline"
            aria-label={`Trace ${run.trace_id}`}
          >
            {shortId(run.trace_id, 10)}
          </Link>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <RelativeTime iso={run.started_at} />
            {source ? (
              <Badge size="sm" variant="neutral">
                {source}
              </Badge>
            ) : null}
          </span>
        </div>
        <span className="@[40rem]:hidden">
          <RunStatus state={runState(run)} />
        </span>
      </div>
      <div className={cn("hidden @[40rem]:block", RUN_COLUMNS.status)}>
        <RunStatus state={runState(run)} />
      </div>
      <div className={RUN_COLUMNS.result}>
        <RunResult run={run} booleanScores={booleanScores} />
      </div>
      <div className={cn("text-xs", RUN_COLUMNS.cost)}>
        <span className="text-muted-foreground @[40rem]:sr-only">Cost </span>
        <RunCost run={run} />
      </div>
    </li>
  );
}
