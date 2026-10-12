import { errorMessage, isApiError } from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

/** The §11 copy for the reserved label; the dialog renders `latest` in mono. */
export const LATEST_RESERVED_REST = " is reserved. It always means the newest version.";

export const SAVE_DISABLED_HINT = "Change the template to save a new version.";

export const PROMPT_IN_USE_MESSAGE =
  "Can't delete this prompt. An evaluator or an experiment uses one of its versions. Remove them first.";

const COPY: Record<string, string> = {
  NAME_TAKEN: "A prompt with this name already exists in this project. Pick another name.",
  KIND_MISMATCH: "A new version must keep the prompt's kind.",
  NO_CHANGES:
    "This is the same as the latest version. Change the template or the model config to save a new version.",
  PROMPT_IN_USE: PROMPT_IN_USE_MESSAGE,
  LABEL_RESERVED: `latest${LATEST_RESERVED_REST}`,
  RATE_LIMITED: "This prompt got its 100 new versions for today. Try again after midnight UTC.",
  FORBIDDEN: roleBlockedCopy("change prompts"),
};

/**
 * Human copy for a failed prompt write. Known problem codes get a sentence of their own; any other
 * failure falls back to the server's own explanation, never to the raw code.
 */
export function promptErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    const copy = COPY[error.code];
    if (copy !== undefined) {
      return copy;
    }
    if (error.status === 404) {
      return "This prompt or version no longer exists. Reload the page.";
    }
  }
  return errorMessage(error);
}

export function isProblem(error: unknown, code: string): boolean {
  return isApiError(error) && error.code === code;
}
