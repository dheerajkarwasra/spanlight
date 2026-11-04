import type { ExperimentParams, ExperimentTarget } from "@/lib/api";

import { PARAM_LABELS, paramValue, targetLabel, type VersionIndex } from "./run-label";

/**
 * What differs between two runs' targets, as the compare page lists it: "temperature 0.2 → 0.7",
 * "model a → b", "messages differ". Empty when both sent the same thing.
 */
export function targetChanges(
  a: ExperimentTarget,
  b: ExperimentTarget,
  index: VersionIndex,
): string[] {
  const changes: string[] = [];
  const labelA = targetLabel(a, index);
  const labelB = targetLabel(b, index);
  if (labelA !== labelB) {
    changes.push(`target ${labelA} → ${labelB}`);
  } else if (
    a.kind === "prompt_version" &&
    b.kind === "prompt_version" &&
    a.prompt_version_id !== b.prompt_version_id
  ) {
    // Both versions not looked up yet: the label is the same placeholder.
    changes.push("prompt version differs");
  }
  if (a.model !== b.model) {
    changes.push(`model ${a.model} → ${b.model}`);
  }
  for (const key of Object.keys(PARAM_LABELS) as (keyof ExperimentParams)[]) {
    const from = paramValue(key, a.params);
    const to = paramValue(key, b.params);
    if (from !== to) changes.push(`${PARAM_LABELS[key]} ${from} → ${to}`);
  }
  if (
    a.kind === "messages" &&
    b.kind === "messages" &&
    JSON.stringify(a.messages) !== JSON.stringify(b.messages)
  ) {
    changes.push("messages differ");
  }
  return changes;
}
