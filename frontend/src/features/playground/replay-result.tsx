import { Link } from "@tanstack/react-router";
import {
  ArrowUpRight,
  CircleCheck,
  CircleDashed,
  Database,
  Info,
  PencilLine,
  Wallet,
} from "lucide-react";
import type { ReactNode } from "react";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AddToDatasetDialog } from "@/features/datasets";
import { useProjectParams, usePermission } from "@/features/shell";
import type { ReplayResult, TraceDetail } from "@/lib/api";

import { ProblemTile } from "./result-card";
import type { ReplayRequestPair } from "./replay-input";
import { ResultMetrics } from "./result-metrics";
import {
  GATEWAY_BUDGET_SKIPPED,
  REPLAY_BUDGET_SKIPPED,
  type ReplayOutcome,
} from "./replay-outcome";
import { ReplayOutputPanes } from "./replay-output-panes";
import { useReplayBudgetQuery } from "./replay-queries";
import { budgetSpentDetail, fidelityLine, IDENTICAL_LINE } from "./replay-state";

export const NOT_STREAMED_REASON = "Not measured: replays are not streamed.";

interface ReplayResultViewProps {
  outcome: ReplayOutcome;
  /** The source trace, for "Save as dataset item"; undefined while it loads or when it is gone. */
  trace: TraceDetail | undefined;
  spanId: string;
  request: ReplayRequestPair;
}

function TraceLink({ traceId, spanId }: { traceId: string; spanId: string | null }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId }}
      search={spanId ? { span: spanId } : {}}
      className="inline-flex items-center gap-1 rounded-sm text-xs font-semibold text-accent hover:underline"
    >
      Open trace
      <ArrowUpRight aria-hidden className="size-3" />
    </Link>
  );
}

function Skipped({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="status" className="flex items-start gap-2.5 rounded-tile bg-surface-muted p-4">
      <Wallet aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-label font-semibold text-foreground">{title}</p>
        <p className="text-xs leading-5 text-muted-foreground">{children}</p>
      </div>
    </div>
  );
}

function ReplayBudgetSkipped() {
  const budget = useReplayBudgetQuery({ enabled: true });
  return <Skipped title={REPLAY_BUDGET_SKIPPED}>{budgetSpentDetail(budget.data)}</Skipped>;
}

function GatewayBudgetSkipped() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Skipped title={GATEWAY_BUDGET_SKIPPED}>
      <Link
        to="/$orgId/$projectId/budgets"
        params={{ orgId, projectId }}
        className="rounded-sm font-semibold text-accent hover:underline"
      >
        Budgets
      </Link>
      : the blocking budget names the limit and when it resets. No answer was charged.
    </Skipped>
  );
}

function FidelityNotice({ result, request }: { result: ReplayResult; request: ReplayRequestPair }) {
  const line = fidelityLine(result, request.before, request.after);
  if (line === IDENTICAL_LINE) {
    return (
      <Notice tone="success" icon={CircleCheck}>
        {line}
      </Notice>
    );
  }
  if (result.fidelity === "modified") {
    return (
      <Notice tone="warning" icon={PencilLine}>
        {line}
      </Notice>
    );
  }
  return <Notice icon={result.fidelity === "messages_only" ? Info : CircleDashed}>{line}</Notice>;
}

function Done({
  result,
  trace,
  spanId,
  request,
}: ReplayResultViewProps & { result: ReplayResult }) {
  const canSave = usePermission("datasets:write");
  const span = trace?.spans.find((candidate) => candidate.span_id === spanId) ?? null;
  return (
    <div className="flex flex-col gap-3.5">
      <FidelityNotice result={result} request={request} />
      <ReplayOutputPanes
        recorded={result.source.output_text}
        replayed={result.output}
        recordedTruncated={span?.truncated ?? false}
      />
      <ResultMetrics result={result} notMeasuredReason={NOT_STREAMED_REASON} />
      <div className="flex flex-wrap items-center gap-3">
        {canSave && trace && span ? (
          <AddToDatasetDialog
            target={{ kind: "trace", trace, selectedSpan: span }}
            trigger={
              <Button variant="secondary" size="sm">
                <Database aria-hidden />
                Save as dataset item
              </Button>
            }
          />
        ) : null}
        {result.trace_id ? <TraceLink traceId={result.trace_id} spanId={result.span_id} /> : null}
      </div>
    </div>
  );
}

/** Figma "Playground / Replay column": running, result, refused and budget-skipped states. */
export function ReplayResultView(props: ReplayResultViewProps) {
  const { outcome } = props;
  return (
    <section aria-label="Replay result" aria-busy={outcome.status === "running"} aria-live="polite">
      {outcome.status === "running" ? (
        <div role="status" className="flex flex-col gap-2.5">
          <span className="sr-only">Replaying</span>
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-11/12" />
          <Skeleton className="h-3.5 w-3/5" />
        </div>
      ) : null}
      {outcome.status === "skipped" && outcome.budget === "replay" ? <ReplayBudgetSkipped /> : null}
      {outcome.status === "skipped" && outcome.budget === "gateway" ? (
        <GatewayBudgetSkipped />
      ) : null}
      {outcome.status === "failed" ? (
        <div className="flex flex-col gap-2">
          <ProblemTile problem={outcome.problem} />
          {outcome.traceId ? <TraceLink traceId={outcome.traceId} spanId={null} /> : null}
        </div>
      ) : null}
      {outcome.status === "done" ? <Done {...props} result={outcome.result} /> : null}
    </section>
  );
}
