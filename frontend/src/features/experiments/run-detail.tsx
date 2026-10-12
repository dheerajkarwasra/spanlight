import { getRouteApi, Link } from "@tanstack/react-router";

import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Experiment, ExperimentRun } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";

import { BackToExperiment } from "./back-link";
import {
  useDatasetInputs,
  useEvaluatorsQuery,
  useExperimentQuery,
  useRunItemsQuery,
  useRunQuery,
  useRunsQuery,
  useScoreConfigsQuery,
} from "./experiment-queries";
import { RunItems } from "./run-items";
import {
  formatElapsed,
  isActive,
  isFinished,
  runDurationMs,
  runEndReason,
  runName,
  targetLabel,
} from "./run-label";
import { RunScores } from "./run-scores";
import { RunStatusPill } from "./run-status-pill";
import { RunTotals } from "./run-totals";
import { scoreMetas } from "./score-meta";
import { targetVersionIds, useVersionIndex } from "./use-version-index";

const route = getRouteApi("/_authed/$orgId/$projectId/experiments/$experimentId/runs/$runId");

/** Figma "Experiments — Run": one run's totals, scores and items. */
export function RunDetailPage() {
  const { experimentId, runId } = route.useParams();
  const experiment = useExperimentQuery(experimentId);
  const run = useRunQuery(experimentId, runId);
  if (experiment.isPending || run.isPending) {
    return (
      <div role="status" aria-label="Loading run" className="flex flex-col gap-6">
        <Skeleton className="h-24 rounded-card" />
        <Skeleton className="h-28 rounded-card" />
        <Skeleton className="h-96 rounded-card" />
      </div>
    );
  }
  if (experiment.isError || run.isError) {
    return (
      <ErrorState
        error={experiment.error ?? run.error}
        title="Couldn't load this run"
        onRetry={() => void Promise.all([experiment.refetch(), run.refetch()])}
      />
    );
  }
  return <RunView experiment={experiment.data} run={run.data} />;
}

function RunView({ experiment, run }: { experiment: Experiment; run: ExperimentRun }) {
  const runs = useRunsQuery(experiment.id);
  const evaluators = useEvaluatorsQuery();
  const configs = useScoreConfigsQuery();
  const inputs = useDatasetInputs(experiment.dataset_id);
  const index = useVersionIndex(targetVersionIds([run.target]));
  const items = useRunItemsQuery(experiment.id, run);
  const seen = Object.keys(run.totals?.scores ?? {});
  const scores = scoreMetas(experiment, evaluators.data ?? [], configs.data, seen);
  // The run just before this one that ended: the natural baseline to compare against.
  const ordered = runs.data ?? [];
  const position = ordered.findIndex((candidate) => candidate.id === run.id);
  const baseline = ordered.slice(position + 1).find((candidate) => isFinished(candidate));
  const took = formatElapsed(runDurationMs(run));
  const subtitle = [
    targetLabel(run.target, index),
    run.target.model,
    `started ${formatRelativeTime(run.started_at) ?? ""}`,
    took ? `${isActive(run) ? "running for" : "took"} ${took}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <BackToExperiment experimentId={experiment.id} name={experiment.name} />
        <PageHeader
          title={
            <span className="inline-flex flex-wrap items-center gap-3">
              {runName(run.number)}
              <RunStatusPill run={run} />
            </span>
          }
          description={subtitle}
          actions={
            baseline && isFinished(run) ? (
              <Button asChild>
                <Link
                  to="/$orgId/$projectId/experiments/$experimentId/compare"
                  from="/$orgId/$projectId/experiments/$experimentId/runs/$runId"
                  params={(prev) => prev}
                  search={(prev) => ({ ...prev, a: baseline.id, b: run.id })}
                >
                  Compare with another run
                </Link>
              </Button>
            ) : null
          }
        />
      </div>
      {run.status === "failed" ? (
        <Notice tone={run.error === "cost_cap_reached" ? "warning" : "danger"}>
          {runEndReason(run.error)}. The finished items are kept.
        </Notice>
      ) : null}
      <RunTotals run={run} />
      {run.totals ? <RunScores totals={run.totals} scores={scores} /> : null}
      <RunItems query={items} run={run} scores={scores} inputs={inputs.data} />
    </div>
  );
}
