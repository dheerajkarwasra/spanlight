/** What each score of an experiment is: its kind, its evaluator's kind and its categories. Pure. */
import type { Evaluator, EvaluatorKind, Experiment, ScoreConfig } from "@/lib/api";

import { scoreKind, type ScoreKind } from "./compare-view";

export interface ScoreMeta {
  name: string;
  kind: ScoreKind;
  /** Null when no evaluator of the project has this name (deleted, or a score from elsewhere). */
  evaluatorKind: EvaluatorKind | null;
  /** A categorical score's configured categories, best first; empty when open. */
  categories: string[];
}

/**
 * The scores in display order: the experiment's evaluators in the order they were attached,
 * then any other score name the runs carry, alphabetically. Scores are named after evaluators.
 */
export function scoreMetas(
  experiment: Experiment,
  evaluators: readonly Evaluator[],
  configs: readonly ScoreConfig[] | undefined,
  seen: readonly string[] = [],
  samples: (name: string) => readonly unknown[] = () => [],
): ScoreMeta[] {
  const byId = new Map(evaluators.map((evaluator) => [evaluator.id, evaluator]));
  const attached = experiment.evaluator_ids.flatMap((id) => byId.get(id)?.name ?? []);
  const rest = seen.filter((name) => !attached.includes(name)).sort();
  return [...new Set([...attached, ...rest])].map((name) => ({
    name,
    kind: scoreKind(name, configs, samples(name)),
    evaluatorKind: evaluators.find((evaluator) => evaluator.name === name)?.config.kind ?? null,
    categories: configs?.find((config) => config.name === name)?.categories ?? [],
  }));
}

/** The score a sentence or a sort leads with: the first one with an average, else the first. */
export function leadScore(metas: readonly ScoreMeta[]): ScoreMeta | null {
  return metas.find((meta) => meta.kind !== "categorical") ?? metas[0] ?? null;
}
