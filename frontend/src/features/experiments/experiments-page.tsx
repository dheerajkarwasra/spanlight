import { Link } from "@tanstack/react-router";
import { FlaskRound, Info } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { SectionCard } from "@/components/section-card";
import { ColumnLabels, TileList, TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { usePermission, useProjectParams } from "@/features/shell";
import type { Experiment } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { useDatasetsQuery, useEvaluatorsQuery, useExperimentsQuery } from "./experiment-queries";
import { EXPERIMENT_GRID, ExperimentRow } from "./experiment-row";
import { isActive } from "./run-label";
import { targetVersionIds, useVersionIndex } from "./use-version-index";

const DESCRIPTION =
  "Run a dataset through a prompt and a model, score every item, then compare runs side by side. Every run makes real provider calls.";

function NewExperimentButton() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Button variant="primary" asChild>
      <Link to="/$orgId/$projectId/experiments/new" params={{ orgId, projectId }}>
        New experiment
      </Link>
    </Button>
  );
}

/** Figma "Experiments — List": every experiment with its dataset, target, scores and last run. */
export function ExperimentsPage() {
  const canRun = usePermission("experiments:run");
  const query = useExperimentsQuery();
  const experiments = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Experiments"
        description={DESCRIPTION}
        actions={canRun && experiments.length > 0 ? <NewExperimentButton /> : null}
      />
      {query.isPending ? (
        <SectionCard title="Experiments">
          <TileListSkeleton label="Loading experiments" rows={4} className="h-[76px]" />
        </SectionCard>
      ) : query.isError && experiments.length === 0 ? (
        <SectionCard title="Experiments">
          <ErrorState
            compact
            error={query.error}
            title="Couldn't load experiments"
            onRetry={() => void query.refetch()}
            className="rounded-tile bg-surface-muted"
          />
        </SectionCard>
      ) : experiments.length === 0 ? (
        <SectionCard title="Experiments">
          <EmptyState
            icon={FlaskRound}
            title="No experiments yet"
            description="An experiment runs every item of a dataset through a prompt and a model, scores the results and lets you compare two runs."
            action={canRun ? <NewExperimentButton /> : null}
            className="rounded-tile bg-surface-muted"
          />
        </SectionCard>
      ) : (
        <ExperimentList
          experiments={experiments}
          hasNextPage={query.hasNextPage}
          isFetchingNextPage={query.isFetchingNextPage}
          onLoadMore={() => void query.fetchNextPage()}
        />
      )}
    </div>
  );
}

interface ExperimentListProps {
  experiments: Experiment[];
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
}

function ExperimentList({
  experiments,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
}: ExperimentListProps) {
  const datasets = useDatasetsQuery();
  const evaluators = useEvaluatorsQuery();
  const index = useVersionIndex(targetVersionIds(experiments.map((item) => item.config.target)));
  const datasetById = new Map((datasets.data ?? []).map((dataset) => [dataset.id, dataset]));
  const evaluatorById = new Map((evaluators.data ?? []).map((item) => [item.id, item]));
  const running = experiments.filter(
    (experiment) => experiment.last_run && isActive(experiment.last_run),
  ).length;
  const count = pluralize(experiments.length, "experiment");

  return (
    <SectionCard
      title="Experiments"
      description={running > 0 ? `${count} · ${running} running` : count}
      className="gap-3"
    >
      <ColumnLabels className={`px-5 lg:grid ${EXPERIMENT_GRID}`}>
        <span>Experiment</span>
        <span>Dataset</span>
        <span>Target</span>
        <span>Evaluators</span>
        <span>Last run</span>
        <span />
      </ColumnLabels>
      <TileList label="Experiments">
        {experiments.map((experiment) => (
          <ExperimentRow
            key={experiment.id}
            experiment={experiment}
            dataset={datasetById.get(experiment.dataset_id)}
            evaluators={evaluatorById}
            index={index}
          />
        ))}
      </TileList>
      {hasNextPage ? (
        <ListFooter
          layout="card"
          summary={`Showing ${count}`}
          hasNextPage={hasNextPage}
          isFetchingNextPage={isFetchingNextPage}
          onLoadMore={onLoadMore}
        />
      ) : null}
      <p className="flex items-start gap-2 pt-1 text-xs text-muted-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0" />
        Each run is capped by its own cost limit. A run that reaches the limit stops and keeps the
        items it finished.
      </p>
    </SectionCard>
  );
}
