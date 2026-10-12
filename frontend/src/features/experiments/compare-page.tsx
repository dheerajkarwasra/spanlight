import { getRouteApi } from "@tanstack/react-router";
import { GitCompareArrows, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError, type Experiment } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { BackToExperiment } from "./back-link";
import { ComparePickers } from "./compare-pickers";
import { CompareResult } from "./compare-result";
import { sideOf } from "./compare-sides";
import {
  useCompareQuery,
  useDatasetInputs,
  useExperimentQuery,
  useRunQuery,
  useRunsQuery,
} from "./experiment-queries";
import { isFinished } from "./run-label";
import { useScoreMetas } from "./use-score-metas";
import { targetVersionIds, useVersionIndex } from "./use-version-index";

const route = getRouteApi("/_authed/$orgId/$projectId/experiments/$experimentId/compare");

/** Figma "Experiments — Compare": two runs of one experiment, score by score and item by item. */
export function ComparePage() {
  const { experimentId } = route.useParams();
  const experiment = useExperimentQuery(experimentId);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <BackToExperiment experimentId={experimentId} name={experiment.data?.name} />
        <PageHeader
          title="Compare runs"
          description="Higher is better for every score. Items are sorted by the size of the change."
        />
      </div>
      {experiment.isPending ? (
        <Skeleton className="h-28 rounded-card" />
      ) : experiment.isError ? (
        <ErrorState
          error={experiment.error}
          title="Couldn't load this experiment"
          onRetry={() => void experiment.refetch()}
        />
      ) : (
        <Comparison experiment={experiment.data} />
      )}
    </div>
  );
}

function Comparison({ experiment }: { experiment: Experiment }) {
  const { a, b, score } = route.useSearch();
  const navigate = route.useNavigate();
  const runs = useRunsQuery(experiment.id);
  const index = useVersionIndex(targetVersionIds((runs.data ?? []).map((run) => run.target)));
  const runA = useRunQuery(experiment.id, a);
  const runB = useRunQuery(experiment.id, b);
  // The polled run reads flip this when a run ends, so the comparison then loads by itself.
  const ready = Boolean(runA.data && runB.data && isFinished(runA.data) && isFinished(runB.data));
  const compare = useCompareQuery(experiment.id, a, b, ready);
  const inputs = useDatasetInputs(experiment.dataset_id);
  const items = compare.data?.items ?? [];
  const metas = useScoreMetas(experiment, Object.keys(compare.data?.aggregate ?? {}), (name) =>
    items.flatMap((item) => [item.a?.scores[name], item.b?.scores[name]]),
  ).filter((meta) => meta.name in (compare.data?.aggregate ?? {}));
  const pair = {
    a: sideOf(
      runA,
      runs.data?.find((run) => run.id === a),
    ),
    b: sideOf(
      runB,
      runs.data?.find((run) => run.id === b),
    ),
  };

  const pickers = runs.data ? (
    <ComparePickers
      runs={runs.data}
      index={index}
      pair={{ a, b, runA: runA.data, runB: runB.data }}
      onChange={(nextA, nextB) =>
        void navigate({ search: (prev) => ({ ...prev, a: nextA, b: nextB }) })
      }
    />
  ) : runs.isError ? (
    <ErrorState
      compact
      error={runs.error}
      title="Couldn't load runs"
      onRetry={() => void runs.refetch()}
    />
  ) : (
    <Skeleton className="h-28 rounded-card" />
  );

  function body(): ReactNode {
    if (a === undefined || b === undefined || a === b) {
      return (
        <EmptyState
          icon={GitCompareArrows}
          title="Pick two runs to compare"
          description={
            a !== undefined && a === b
              ? "Baseline and candidate are the same run. Pick two different runs."
              : "Scores, cost and latency are compared across the two runs, then every item is shown with both outputs side by side."
          }
          className="rounded-card bg-surface-muted"
        />
      );
    }
    if (runA.isError || runB.isError) {
      const error = runA.error ?? runB.error;
      return (
        <ErrorState
          error={error}
          title={
            isApiError(error) && error.status === 404
              ? "These runs aren't in this experiment"
              : "Couldn't load these runs"
          }
          onRetry={() => void Promise.all([runA.refetch(), runB.refetch()])}
        />
      );
    }
    if (runA.data && runB.data && !ready) {
      return (
        <Notice tone="warning" role="status">
          One of these runs is still queued or running. The comparison loads here when it ends.
        </Notice>
      );
    }
    if (compare.isError) {
      const busy = isApiError(compare.error) && compare.error.code === "RUN_IN_PROGRESS";
      return busy ? (
        <div className="flex flex-col items-start gap-3">
          <Notice tone="warning" role="status">
            One of these runs is still queued or running. Compare it once it ends.
          </Notice>
          <Button size="sm" onClick={() => void compare.refetch()}>
            <RotateCw aria-hidden />
            Try again
          </Button>
        </div>
      ) : (
        <ErrorState
          error={compare.error}
          title="Couldn't compare these runs"
          onRetry={() => void compare.refetch()}
        />
      );
    }
    if (!compare.data) {
      return (
        <div role="status" aria-label="Comparing runs" className="flex flex-col gap-4">
          <Skeleton className="h-40 rounded-card" />
          <Skeleton className="h-56 rounded-card" />
          <Skeleton className="h-96 rounded-card" />
        </div>
      );
    }
    const inBoth = compare.data.items.filter((item) => item.a && item.b).length;
    return (
      <CompareResult
        comparison={compare.data}
        pair={pair}
        metas={metas}
        sort={{
          score,
          onChange: (next) =>
            void navigate({ search: (prev) => ({ ...prev, score: next }), replace: true }),
          detail: `${pair.b.name} against ${pair.a.name} · ${pluralize(inBoth, "item")} in both runs`,
        }}
        inputs={inputs.data}
      />
    );
  }

  return (
    <>
      {pickers}
      {body()}
    </>
  );
}
