import { REPLAY_OFF_REASON } from "@/features/traces/replay-button-state";
import { isApiError, type ReplayResult } from "@/lib/api";

import { problemOf, requestProblem, type ResultProblem } from "./result-error";

/**
 * Where the replay column is after "Run replay". A spent budget, the replay one or a project's
 * gateway block, is a neutral "skipped", not an error: nothing failed, the call was not made.
 */
export type ReplayOutcome =
  | { status: "idle" }
  | { status: "running" }
  | { status: "done"; result: ReplayResult }
  | { status: "skipped"; budget: "replay" | "gateway"; traceId: string | null }
  | { status: "failed"; problem: ResultProblem; traceId: string | null };

export const REPLAY_BUDGET_SKIPPED = "Skipped: this month's replay budget is spent.";
export const GATEWAY_BUDGET_SKIPPED = "Skipped: a project budget blocks gateway calls right now.";

/** Titles of the `422 REPLAY_*` refusals; the server's detail is the explanation. */
const REFUSAL_TITLES: Record<string, string> = {
  REPLAY_PROVIDER_MISMATCH: "This credential cannot replay this span",
  REPLAY_MODEL_UNPRICED: "No price for this model",
  REPLAY_PAYLOAD_REDACTED: "The request still has redacted parts",
  REPLAY_PAYLOAD_TRUNCATED: "The stored request was cut",
  REPLAY_BODY_TOO_LARGE: "The edited request is too large",
  REPLAY_UNSUPPORTED_INPUT: "This is not a request the provider takes",
  REPLAY_NO_PAYLOAD: "No stored request",
  REPLAY_NOT_LLM: "Not an LLM call",
};

/** The outcome of an answered replay: its `error`, if any, says what stopped the call. */
export function outcomeOfResult(result: ReplayResult): ReplayOutcome {
  if (result.error === null) {
    return { status: "done", result };
  }
  if (result.error === "BUDGET_EXCEEDED") {
    return { status: "skipped", budget: "gateway", traceId: result.trace_id };
  }
  const problem: ResultProblem =
    result.error === "replay_failed"
      ? {
          tone: "danger",
          title: "The replay failed",
          detail: "The call did not finish, so there is no answer to show. Run it again.",
          label: "replay_failed",
        }
      : problemOf(result.error);
  return { status: "failed", problem, traceId: result.trace_id };
}

/** The outcome of a replay request the server refused before (or instead of) calling. */
export function outcomeOfError(error: unknown): ReplayOutcome {
  if (isApiError(error)) {
    if (error.code === "REPLAY_BUDGET_EXCEEDED") {
      return { status: "skipped", budget: "replay", traceId: null };
    }
    const title =
      REFUSAL_TITLES[error.code] ?? (error.code.startsWith("REPLAY_") ? "Replay refused" : null);
    if (title !== null) {
      const problem: ResultProblem = {
        tone: "danger",
        title,
        detail: error.detail ?? error.title,
        label: error.code,
      };
      return { status: "failed", problem, traceId: null };
    }
    if (error.code === "NOT_CONFIGURED") {
      const problem: ResultProblem = {
        tone: "warning",
        title: "Replay is turned off",
        detail: error.detail ?? REPLAY_OFF_REASON,
        label: null,
      };
      return { status: "failed", problem, traceId: null };
    }
    if (error.status === 404) {
      const problem: ResultProblem = {
        tone: "danger",
        title: "Nothing to replay",
        detail: "The span or the credential no longer exists. Reload and pick again.",
        label: null,
      };
      return { status: "failed", problem, traceId: null };
    }
  }
  return { status: "failed", problem: requestProblem(error), traceId: null };
}
