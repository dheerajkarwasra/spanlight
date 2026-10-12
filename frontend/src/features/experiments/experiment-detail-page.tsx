import { getRouteApi, Link } from "@tanstack/react-router";

import { DisabledReason } from "@/components/disabled-reason";
import { ErrorState } from "@/components/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { usePermission } from "@/features/shell";
import type { Experiment } from "@/lib/api";

import { ExperimentHeader } from "./experiment-header";
import {
  useDatasetQuery,
  useEvaluatorsQuery,
  useExperimentQuery,
  useRunQuery,
  useRunsQuery,
  useScoreConfigsQuery,
} from "./experiment-queries";
import { RunList } from "./run-list";
import { RunProgress, RunStopped } from "./run-progress";
import { comparePair, isActive, liveTicks } from "./run-label";
import { leadScore, scoreMetas } from "./score-meta";
import { StartRun } from "./start-run";
import { targetVersionIds, useVersionIndex } from "./use-version-index";

const route = getRouteApi("/_authed/$orgId/$projectId/experiments/$experimentId");

/** Figma "Experiments — Detail": the experiment, its newest run's progress and every run. */
export function ExperimentDetailPage() {
  const { experimentId } = route.useParams();
  const experiment = useExperimentQuery(experimentId);
  if (experiment.isPending) {
    return (
      <div role="status" aria-label="Loading experiment" className="flex flex-col gap-6">
        <Skeleton className="h-28 rounded-card" />
        <Skeleton className="h-72 rounded-card" />
      </div>
    );
  }
  if (experiment.isError) {
    return (
      <ErrorState
        error={experiment.error}
        title="Couldn't load this experiment"
        onRetry={() => void experiment.refetch()}
      />
    );
  }
  return <Detail experiment={experiment.data} />;
}

function Detail({ experiment }: { experiment: Experiment }) {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const canRun = usePermission("experiments:run");
  const runs = useRunsQuery(experiment.id);
  const dataset = useDatasetQuery(experiment.dataset_id);
  const evaluators = useEvaluatorsQuery();
  const configs = useScoreConfigsQuery();
  const index = useVersionIndex(
    targetVersionIds([experiment.config.target, ...(runs.data ?? []).map((run) => run.target)]),
  );
  const latest = runs.data?.[0];
  // Lists carry stored totals (null until a run ends); a running run is read live.
  const live = useRunQuery(experiment.id, latest && isActive(latest) ? latest.id : undefined);
  const scores = scoreMetas(experiment, evaluators.data ?? [], configs.data);
  const expectedItems = dataset.data
    ? Math.min(dataset.data.item_count, experiment.config.max_items)
    : null;
  const asked = [search.a, search.b].filter((id): id is string => id !== undefined);
  // A ticked run that no longer exists (or never ended) is dropped, not kept as a dead tick.
  const ticked = runs.data ? liveTicks(runs.data, asked) : asked;
  const pair = comparePair(runs.data ?? [], ticked);
  const busy = runs.data?.some((run) => isActive(run)) ?? false;

  function tick(runId: string, next: boolean) {
    const kept = ticked.filter((id) => id !== runId);
    // Ticking a third run lets go of the one ticked first.
    const chosen = next ? [...kept, runId].slice(-2) : kept;
    void navigate({
      search: (prev) => ({ ...prev, a: chosen[0], b: chosen[1] }),
      replace: true,
    });
  }

  const compare = pair ? (
    <Button asChild>
      <Link
        from="/$orgId/$projectId/experiments/$experimentId"
        to="/$orgId/$projectId/experiments/$experimentId/compare"
        search={(prev) => ({ ...prev, a: pair[0], b: pair[1] })}
      >
        Compare runs
      </Link>
    </Button>
  ) : (
    <DisabledReason reason="Two finished runs are needed to compare.">
      <Button disabled>Compare runs</Button>
    </DisabledReason>
  );

  const liveRun = live.data && latest?.id === live.data.id ? live.data : undefined;
  return (
    <div className="flex flex-col gap-6">
      <ExperimentHeader
        experiment={experiment}
        dataset={dataset.data}
        scores={scores}
        index={index}
        actions={
          <>
            {compare}
            {canRun ? <StartRun experiment={experiment} index={index} busy={busy} /> : null}
          </>
        }
      />
      {liveRun && isActive(liveRun) ? (
        <RunProgress
          run={liveRun}
          expectedItems={expectedItems}
          maxCost={experiment.config.max_cost_usd}
        />
      ) : latest?.status === "failed" ? (
        <RunStopped
          run={latest}
          expectedItems={expectedItems}
          maxCost={experiment.config.max_cost_usd}
        />
      ) : null}
      <RunList
        runs={{ data: runs.data, error: runs.error, retry: () => void runs.refetch() }}
        context={{
          experimentId: experiment.id,
          index,
          expectedItems,
          score: leadScore(scores),
          liveRun,
        }}
        selection={{ ticked, onTick: tick }}
      />
    </div>
  );
}
