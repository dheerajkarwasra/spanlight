import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { useProjectParams } from "@/features/shell";
import type { Dataset, Evaluator, Experiment, LastRun } from "@/lib/api";
import { formatRelativeTime, pluralize, shortId } from "@/lib/format";

import { RunStatusPill } from "./run-status-pill";
import { runName, type VersionIndex } from "./run-label";
import { TargetText } from "./target-text";

/** The grid shared by the column labels and the rows of the experiments list. */
export const EXPERIMENT_GRID =
  "lg:grid lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,1.1fr)_minmax(0,0.9fr)_20px] lg:items-center lg:gap-4";

interface ExperimentRowProps {
  experiment: Experiment;
  dataset: Dataset | undefined;
  evaluators: ReadonlyMap<string, Evaluator>;
  index: VersionIndex;
}

/** One experiment as a tile (Figma "Experiments — List" row); the whole tile opens it. */
export function ExperimentRow({ experiment, dataset, evaluators, index }: ExperimentRowProps) {
  const { orgId, projectId } = useProjectParams();
  // A deleted evaluator is skipped by new runs, so it is not listed.
  const names = experiment.evaluator_ids.flatMap((id) => evaluators.get(id)?.name ?? []);
  return (
    <li>
      <Link
        to="/$orgId/$projectId/experiments/$experimentId"
        params={{ orgId, projectId, experimentId: experiment.id }}
        className={`flex flex-col gap-3 rounded-tile bg-surface-muted px-5 py-4 transition-colors hover:bg-surface-hover ${EXPERIMENT_GRID}`}
      >
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-[15px] font-semibold text-foreground">
            {experiment.name}
          </span>
          <span className="text-xs text-muted-foreground">
            Created {formatRelativeTime(experiment.created_at)}
          </span>
        </div>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate font-mono text-xs text-foreground">
            <span className="sr-only">Dataset </span>
            {dataset?.name ?? shortId(experiment.dataset_id)}
          </span>
          {dataset ? (
            <span className="text-xs text-muted-foreground">
              {pluralize(dataset.item_count, "item")}
            </span>
          ) : null}
        </div>
        <TargetText target={experiment.config.target} index={index} />
        <div className="flex flex-wrap gap-1.5">
          {names.length === 0 ? (
            <span className="text-xs text-muted-foreground">No evaluators</span>
          ) : (
            names.map((name, at) => (
              <span
                key={`${name}-${at}`}
                className="rounded-full border border-border-strong bg-surface px-2.5 py-0.5 text-xs font-medium text-foreground"
              >
                {name}
              </span>
            ))
          )}
        </div>
        <LastRunCell run={experiment.last_run} />
        <ChevronRight aria-hidden className="hidden size-4 text-muted-foreground lg:block" />
      </Link>
    </li>
  );
}

function LastRunCell({ run }: { run: LastRun | null }) {
  if (run === null) {
    return <span className="text-sm text-muted-foreground">No runs yet</span>;
  }
  return (
    <div className="flex flex-row items-center gap-2 lg:flex-col lg:items-start lg:gap-1">
      <span className="sr-only">Last run, {runName(run.number)}: </span>
      <RunStatusPill run={run} />
      <span className="text-sm text-muted-foreground">{formatRelativeTime(run.started_at)}</span>
    </div>
  );
}
