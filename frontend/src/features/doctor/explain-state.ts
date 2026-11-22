import { errorMessage, isApiError } from "@/lib/api";

/*
 * What the Explain card says and does. The server's refusals map to the Doctor copy; the card
 * renders whatever this returns, so the wording lives in one place.
 */

export const EXPLAIN_LABELS = {
  idle: "Explain with AI",
  loading: "Generating explanation…",
  again: "Explain again",
  retry: "Try again",
} as const;

export const ADVISORY_LABEL = "AI-generated from the evidence above. Verify before acting.";

export const VIEWER_REASON = "Only admins can request explanations.";

export const NOT_CONFIGURED_MESSAGE =
  "Explanations require a credential for the configured provider under Gateway › Credentials.";
export const BUDGET_MESSAGE = "Monthly explanation budget reached. It resets on the 1st.";
export const FAILED_MESSAGE =
  "The provider could not explain this insight. A billable request counts toward this month's budget.";
/** New copy: the spec has no wording for an unpriced explain model. */
export const UNPRICED_MESSAGE =
  "Explanations are unavailable because the configured model's cost cannot be bounded.";

export interface ExplainNotice {
  tone: "info" | "warning" | "danger";
  message: string;
  /** Show the "Open Credentials" link. */
  credentialsLink: boolean;
  /** Asking again cannot work until something changes (a credential, the budget, a price). */
  blocks: boolean;
}

/** The notice for a failed explain request. */
export function explainNotice(error: unknown): ExplainNotice {
  const code = isApiError(error) ? error.code : null;
  switch (code) {
    case "NOT_CONFIGURED":
      return { tone: "info", message: NOT_CONFIGURED_MESSAGE, credentialsLink: true, blocks: true };
    case "EXPLAIN_BUDGET_EXCEEDED":
      return { tone: "warning", message: BUDGET_MESSAGE, credentialsLink: false, blocks: true };
    case "EXPLAIN_MODEL_UNPRICED":
      return { tone: "warning", message: UNPRICED_MESSAGE, credentialsLink: false, blocks: true };
    case "EXPLAIN_FAILED":
      return { tone: "danger", message: FAILED_MESSAGE, credentialsLink: false, blocks: false };
    default:
      return {
        tone: "danger",
        message: errorMessage(error),
        credentialsLink: false,
        blocks: false,
      };
  }
}

export type ExplainMode = "ready" | "busy" | "locked";

export interface ExplainControl {
  label: string;
  mode: ExplainMode;
  /** The ink button: asking for the first explanation, or waiting for it. */
  primary: boolean;
  /** Why the button is locked, when the person (not the state of the system) is the reason. */
  reason: string | null;
}

interface ControlInput {
  canManage: boolean;
  pending: boolean;
  notice: ExplainNotice | null;
  /** The insight already has a stored explanation. */
  hasResult: boolean;
}

/** The button's label and mode. A locked button is aria-disabled but stays focusable. */
export function explainControl({
  canManage,
  pending,
  notice,
  hasResult,
}: ControlInput): ExplainControl {
  const resting = hasResult ? EXPLAIN_LABELS.again : EXPLAIN_LABELS.idle;
  if (!canManage) {
    return { label: resting, mode: "locked", primary: !hasResult, reason: VIEWER_REASON };
  }
  if (pending) {
    return { label: EXPLAIN_LABELS.loading, mode: "busy", primary: true, reason: null };
  }
  if (notice === null) {
    return { label: resting, mode: "ready", primary: !hasResult, reason: null };
  }
  if (notice.blocks) {
    return { label: resting, mode: "locked", primary: !hasResult, reason: null };
  }
  return { label: EXPLAIN_LABELS.retry, mode: "ready", primary: false, reason: null };
}
