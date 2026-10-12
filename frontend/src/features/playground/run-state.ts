import type { PlaygroundResult } from "@/lib/api";

import { problemOf, type ResultProblem } from "./result-error";

/** Where one column is in its run. */
export type RunState =
  | { status: "idle" }
  | { status: "running" }
  | { status: "done"; result: PlaygroundResult }
  | { status: "failed"; problem: ResultProblem; traceId: string | null };

export const IDLE: RunState = { status: "idle" };
export const RUNNING: RunState = { status: "running" };

/** A column whose result carries an `error` failed; the others answered. */
export function runStateOf(result: PlaygroundResult): RunState {
  if (result.error !== null) {
    return { status: "failed", problem: problemOf(result.error), traceId: result.trace_id };
  }
  return { status: "done", result };
}

export function failedState(problem: ResultProblem): RunState {
  return { status: "failed", problem, traceId: null };
}

/** The state of each column after a run of `count` columns came back with `results`. */
export function statesOfRun(results: readonly PlaygroundResult[], count: number): RunState[] {
  return Array.from({ length: count }, (_, index) => {
    const result = results[index];
    return result
      ? runStateOf(result)
      : failedState({
          tone: "danger",
          title: "No result",
          detail: "The server sent no result for this column. Run again.",
          label: null,
        });
  });
}
