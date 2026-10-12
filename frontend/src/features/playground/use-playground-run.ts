import { useState } from "react";

import { COLUMN_LETTERS, firstProblem, toRequestConfigs, type ColumnState } from "./config-state";
import { useRunPlayground } from "./playground-queries";
import { requestProblem } from "./result-error";
import { RUNNING, failedState, statesOfRun, type RunState } from "./run-state";

/**
 * Runs the columns as one request and keeps each column's state. A column that failed keeps
 * only its own error; the other columns keep their answers.
 */
export function usePlaygroundRun(
  columns: readonly ColumnState[],
  credentialIds: readonly string[],
) {
  const mutation = useRunPlayground();
  const [states, setStates] = useState<Record<string, RunState>>({});
  const [notice, setNotice] = useState<string | null>(null);

  async function run() {
    const problem = firstProblem(columns, credentialIds);
    if (problem) {
      setNotice(`Run ${COLUMN_LETTERS[problem.index] ?? ""}: ${problem.message}`);
      return;
    }
    setNotice(null);
    const keys = columns.map((column) => column.key);
    setStates((previous) => ({
      ...previous,
      ...Object.fromEntries(keys.map((k) => [k, RUNNING])),
    }));
    let next: RunState[];
    try {
      const { results } = await mutation.mutateAsync({
        configs: toRequestConfigs(columns, credentialIds),
        key: crypto.randomUUID(),
      });
      next = statesOfRun(results, keys.length);
    } catch (error) {
      next = keys.map(() => failedState(requestProblem(error)));
    }
    const settled = keys.flatMap((key, index) => {
      const state = next[index];
      return state ? [[key, state] as const] : [];
    });
    setStates((previous) => ({ ...previous, ...Object.fromEntries(settled) }));
  }

  return { states, notice, running: mutation.isPending, run };
}
