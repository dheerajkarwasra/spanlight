import { DEFAULT_FAILURE_CATEGORIES, FAILURE_CATEGORY_SCORE } from "@/features/scores";
import { errorMessage, isApiError, type AnnotationQueue, type ReviewPolicyInput } from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

/** The starting categories (shared with the Releases dialog) and the score's name. */
export { DEFAULT_FAILURE_CATEGORIES, FAILURE_CATEGORY_SCORE };

/** The queue the set-up flow creates when no eligible queue is picked. */
export const DAILY_REVIEW_QUEUE = "Daily review";

/** The requests the confirm runs, always in this order. */
export type SetupStep = "create_config" | "create_queue" | "create_policy";

export interface SetupState {
  /** The project's `failure_category` config; null while it has none. */
  configId: string | null;
  /** The eligible queue picked in step 2; null to create "Daily review". */
  queueId: string | null;
}

/** What is still to do: the config only when missing, the queue only when none was picked. */
export function setupPlan(state: SetupState): SetupStep[] {
  return [
    ...(state.configId === null ? (["create_config"] as const) : []),
    ...(state.queueId === null ? (["create_queue"] as const) : []),
    "create_policy",
  ];
}

/** The queues a policy can fill: those that ask for the failure-category config. */
export function eligibleQueues(
  queues: readonly AnnotationQueue[],
  configId: string | null,
): AnnotationQueue[] {
  return configId === null ? [] : queues.filter((q) => q.score_config_ids.includes(configId));
}

/** The days a manual run accepts, newest first: yesterday back to 7 days ago (UTC). */
export function selectableDays(now: Date): string[] {
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(now.getTime());
    day.setUTCDate(day.getUTCDate() - (index + 1));
    return day.toISOString().slice(0, 10);
  });
}

/** Step 3 of the set-up dialog, as the form holds it. */
export interface SelectionDraft {
  /** The random share in percent, 0 to 5. */
  randomPercent: number;
  include_doctor_findings: boolean;
  include_errors: boolean;
  include_cost_outliers: boolean;
  /** The cap as typed; see `capError`. */
  dailyCap: string;
  environments: string[];
}

/** The server's defaults: 1 % random, every reason, 50 a day, every environment. */
export const DEFAULT_SELECTION: SelectionDraft = {
  randomPercent: 1,
  include_doctor_findings: true,
  include_errors: true,
  include_cost_outliers: true,
  dailyCap: "50",
  environments: [],
};

/** Why the typed cap is refused, or null when it is a whole number from 1 to 500. */
export function capError(text: string): string | null {
  const cap = Number(text.trim());
  return text.trim() !== "" && Number.isInteger(cap) && cap >= 1 && cap <= 500
    ? null
    : "Enter a whole number from 1 to 500.";
}

/** Every reason off and no random share: the server answers `NOTHING_SELECTED`. */
export function selectsNothing(draft: SelectionDraft): boolean {
  return (
    draft.randomPercent === 0 &&
    !draft.include_doctor_findings &&
    !draft.include_errors &&
    !draft.include_cost_outliers
  );
}

/** The `PUT /review-policy` body for the draft; the rate as a 4-place fraction. */
export function selectionInput(draft: SelectionDraft, queueId: string): ReviewPolicyInput {
  return {
    queue_id: queueId,
    enabled: true,
    random_rate: (draft.randomPercent / 100).toFixed(4),
    include_doctor_findings: draft.include_doctor_findings,
    include_errors: draft.include_errors,
    include_cost_outliers: draft.include_cost_outliers,
    daily_cap: Number(draft.dailyCap.trim()),
    environments: draft.environments,
  };
}

/** How a step chip of the set-up dialog reads. */
export type ChipState = "done" | "current" | "todo" | "failed";

/** Which of the three chips (categories, queue, selection) each request belongs to. */
export const CHIP_OF_STEP: Record<SetupStep, number> = {
  create_config: 0,
  create_queue: 1,
  create_policy: 2,
};

/** A run of the plan as the chips need it; see `useSetupReview`. */
interface RunShape {
  steps: { step: SetupStep; status: "running" | "done" | "failed" }[];
  done: boolean;
}

/** The chip states while the confirm runs: finished, running, failed, or still to come. */
export function runChipStates(run: RunShape): ChipState[] {
  if (run.done) {
    return ["done", "done", "done"];
  }
  const failed = run.steps.find((entry) => entry.status === "failed");
  const at = failed ?? run.steps.find((entry) => entry.status === "running");
  const limit = at === undefined ? 0 : CHIP_OF_STEP[at.step];
  return [0, 1, 2].map((index) =>
    index < limit ? "done" : index > limit ? "todo" : failed ? "failed" : "current",
  );
}

const STEP_NAMES: Record<SetupStep, string> = {
  create_config: "Creating the failure categories failed",
  create_queue: `Creating the queue “${DAILY_REVIEW_QUEUE}” failed`,
  create_policy: "Creating the policy failed",
};

/** The title of a failed step in the set-up progress, e.g. "Creating the policy failed". */
export function stepFailureTitle(step: SetupStep): string {
  return STEP_NAMES[step];
}

const REVIEW_COPY: Record<string, string> = {
  QUEUE_LACKS_FAILURE_CATEGORY:
    "The queue does not ask for a failure category. Pick a queue that includes the failure_category score.",
  NOTHING_SELECTED:
    "Turn on at least one reason or set a random share above 0%, or the policy selects nothing.",
  ALREADY_SELECTED: "That day was already selected. Its traces are in the queue.",
  DAY_NOT_COMPLETE: "Only a finished UTC day can be selected. Pick yesterday or earlier.",
  DAY_TOO_OLD: "Only the last 7 days can be selected.",
  FORBIDDEN: roleBlockedCopy("change the daily review"),
};

/** Human copy for a failed review-policy write or run; never the raw code. */
export function reviewErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    const copy = REVIEW_COPY[error.code];
    if (copy !== undefined) {
      return copy;
    }
  }
  return errorMessage(error);
}

/** Why one set-up step failed, and that the finished steps are kept for the retry. */
export function stepFailureText(step: SetupStep, error: unknown): string {
  if (isApiError(error) && error.code === "NAME_TAKEN") {
    return step === "create_config"
      ? "A failure_category config already exists. Close this dialog and open it again to use it."
      : `A queue named “${DAILY_REVIEW_QUEUE}” already exists. Go back and pick it, or rename it first.`;
  }
  return `${reviewErrorMessage(error)} Finished steps are kept; try again to finish.`;
}
