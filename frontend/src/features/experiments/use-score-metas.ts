import type { Experiment } from "@/lib/api";

import { useEvaluatorsQuery, useScoreConfigsQuery } from "./experiment-queries";
import { scoreMetas, type ScoreMeta } from "./score-meta";

/**
 * The experiment's scores in display order, with their kinds: reads the evaluators (score names
 * and kinds) and the score configs (numeric, yes / no or categories).
 */
export function useScoreMetas(
  experiment: Experiment,
  seen: readonly string[] = [],
  samples?: (name: string) => readonly unknown[],
): ScoreMeta[] {
  const evaluators = useEvaluatorsQuery();
  const configs = useScoreConfigsQuery();
  return scoreMetas(experiment, evaluators.data ?? [], configs.data, seen, samples);
}
