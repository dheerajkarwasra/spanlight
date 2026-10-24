import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import type { Dataset, Experiment } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";

import { ExperimentsBreadcrumbs } from "./back-link";
import type { VersionIndex } from "./run-label";
import type { ScoreMeta } from "./score-meta";
import { TargetChips } from "./target-text";

interface ExperimentHeaderProps {
  experiment: Experiment;
  dataset: Dataset | undefined;
  scores: readonly ScoreMeta[];
  index: VersionIndex;
  actions: ReactNode;
}

const LABEL = "text-xs font-medium text-muted-foreground";
const CHIP =
  "rounded-full border border-border-strong px-2.5 py-0.5 text-xs font-medium text-foreground";

/** The detail page's title, what the experiment runs on and with, and its actions. */
export function ExperimentHeader({
  experiment,
  dataset,
  scores,
  index,
  actions,
}: ExperimentHeaderProps) {
  return (
    <div className="flex flex-col gap-2">
      <ExperimentsBreadcrumbs current={experiment.name} />
      <PageHeader title={experiment.name} actions={actions} />
      <dl className="flex flex-wrap items-center gap-x-2 gap-y-2">
        <dt className={LABEL}>Dataset</dt>
        <dd className={`${CHIP} font-mono text-[11px]`}>
          {dataset
            ? `${dataset.name} · ${formatInteger(dataset.item_count)} items`
            : experiment.dataset_id.slice(0, 8)}
        </dd>
        <dt className={`${LABEL} ml-2`}>Target</dt>
        <dd className="flex flex-wrap gap-2">
          <TargetChips target={experiment.config.target} index={index} />
        </dd>
        <dt className={`${LABEL} ml-2`}>Scored by</dt>
        <dd className="flex flex-wrap gap-2">
          {scores.length === 0 ? (
            <span className={LABEL}>no evaluators</span>
          ) : (
            scores.map((score) => (
              <span key={score.name} className={CHIP}>
                {score.name}
              </span>
            ))
          )}
        </dd>
        <dt className={`${LABEL} ml-2`}>Cost limit</dt>
        <dd className={LABEL}>{formatCost(experiment.config.max_cost_usd)}</dd>
      </dl>
    </div>
  );
}
