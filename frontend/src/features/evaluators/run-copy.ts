import type { EvaluatorRun, EvaluatorRunStatus } from "@/lib/api";

/*
 * What a run's state and error code mean, in words. The run list shows the human line and the
 * code under it in mono (Figma "Evaluators — Detail"); a code nobody mapped still gets a
 * sentence, never the bare code alone.
 */

/** The run's error when the judge's monthly budget could not cover the call. */
export const BUDGET_EXCEEDED = "budget_exceeded";

/** Spec §11: a run skipped for budget is its own neutral state, not an error. */
export const BUDGET_SKIPPED_COPY = "Skipped: the monthly budget is spent.";

export type RunState = "done" | "failed" | "running" | "queued" | "skipped";

export const RUN_STATE_LABELS: Record<RunState, string> = {
  done: "Done",
  failed: "Failed",
  running: "Running",
  queued: "Queued",
  skipped: "Skipped",
};

/** Done, failed, running, queued, or skipped for a run the budget stopped before any call. */
export function runState(run: Pick<EvaluatorRun, "status" | "error">): RunState {
  if (run.status === "failed" && run.error === BUDGET_EXCEEDED) {
    return "skipped";
  }
  if (run.status === "done" || run.status === "running" || run.status === "queued") {
    return run.status;
  }
  return "failed";
}

const ERROR_COPY: Record<string, string> = {
  [BUDGET_EXCEEDED]: BUDGET_SKIPPED_COPY,
  gateway_budget_blocked: "A project budget blocked the call.",
  verdict_unparseable: "The judge's reply was not a valid score.",
  judge_no_answer: "The judge model returned no answer.",
  judge_template_invalid: "The judge prompt could not be filled from this trace.",
  judge_prompt_too_large:
    "With this trace filled in, the judge prompt is larger than 256 KB, so it was not sent.",
  judge_prompt_missing: "The judge prompt version no longer exists.",
  judge_credential_missing: "The judge's credential no longer exists.",
  judge_provider_unsupported: "The judge's credential is not an Anthropic or OpenAI key.",
  evaluator_model_unpriced: "The judge model has no price, so its budget cannot be enforced.",
  webhook_invalid_reply: "Your endpoint's reply was not in the expected format.",
  webhook_unreachable: "Your endpoint could not be reached.",
  webhook_payload_too_large: "The trace was too large to send to your endpoint.",
  webhook_channel_missing: "The webhook channel no longer exists.",
  webhook_channel_kind: "The channel is no longer a webhook channel.",
  webhook_secret_unreadable: "The channel's signing secret could not be read.",
  evaluator_disabled: "The evaluator was disabled before the run started.",
  abandoned: "The run stopped reporting and was marked as abandoned.",
  cancelled: "The run was cancelled while the call was in flight.",
  regex_timeout: "The pattern took longer than one second and was stopped.",
  trace_not_found: "The trace is no longer stored.",
  no_payload: "The span has no stored input or output.",
  gateway_not_configured: "The gateway is not running on this server.",
  credentials_not_configured: "Provider credentials are not configured on this server.",
  cost_cap_reached: "The experiment's cost limit was reached.",
  internal_error: "Something went wrong on our side while scoring.",
  run_not_started: "The run did not start.",
};

/** One sentence for a run error code (`webhook_status_503` and `judge_status_429` included). */
export function runErrorCopy(code: string): string {
  const known = ERROR_COPY[code];
  if (known) {
    return known;
  }
  const webhook = /^webhook_status_(\d{3})$/.exec(code);
  if (webhook) {
    return `Your endpoint answered with HTTP ${webhook[1]}.`;
  }
  const judge = /^judge_status_(\d{3})$/.exec(code);
  if (judge) {
    return `The provider refused the judge call with HTTP ${judge[1]}.`;
  }
  if (code.startsWith("missing_variables")) {
    return "The judge prompt needs variables this trace does not have.";
  }
  return "The evaluator could not score this trace.";
}

/** A run's charge to 4 decimals ("$0.0009"), or null when unreadable. */
export function formatRunCost(cost: string): string | null {
  const amount = Number(cost);
  return cost.trim() === "" || !Number.isFinite(amount) ? null : `$${amount.toFixed(4)}`;
}

export type RunFilter = "all" | RunState;

export const RUN_FILTERS: readonly RunFilter[] = [
  "all",
  "done",
  "failed",
  "running",
  "queued",
  "skipped",
];

/**
 * The API status a filter asks for. Skipped runs are failed runs with `budget_exceeded`, so
 * "Failed" and "Skipped" both read failed runs and split them with `runState`.
 */
export function runFilterStatus(filter: RunFilter): EvaluatorRunStatus | undefined {
  if (filter === "all") {
    return undefined;
  }
  return filter === "skipped" ? "failed" : filter;
}
