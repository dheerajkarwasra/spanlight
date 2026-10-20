import { errorMessage, isApiError } from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

export const MAX_ITEMS = 10_000;

export const DATASET_FULL_TITLE = "This dataset is full";
export const DATASET_FULL_MESSAGE =
  "A dataset holds up to 10,000 items. Remove items you no longer need, or start a new dataset. New items and uploads are blocked until then.";
export const DATASET_IN_USE_MESSAGE =
  "An experiment uses this dataset, so it cannot be deleted. Delete that experiment first, or keep the dataset.";
export const DATASET_LOCKED_MESSAGE = "This dataset is locked. An admin can unlock it.";

const COPY: Record<string, string> = {
  DATASET_FULL: "This dataset is full. A dataset holds up to 10,000 items.",
  DATASET_IN_USE: DATASET_IN_USE_MESSAGE,
  DATASET_LOCKED: DATASET_LOCKED_MESSAGE,
  UNSUPPORTED_FILE: "Only .jsonl and .csv files of UTF-8 text can be uploaded.",
  PAYLOAD_TOO_LARGE: "The file is larger than 5 MB. Split it into smaller files.",
  FORBIDDEN: roleBlockedCopy("change datasets"),
  RATE_LIMITED: "Too many requests. Wait a moment and try again.",
};

/**
 * Human copy for a failed dataset or queue write. Known problem codes get a sentence of their
 * own; any other failure falls back to the server's own explanation, never to the raw code.
 */
export function datasetErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    const copy = COPY[error.code];
    if (copy !== undefined) {
      return copy;
    }
    if (error.status === 404) {
      return "This dataset or item no longer exists. Reload the page.";
    }
  }
  return errorMessage(error);
}

export function nameTakenMessage(name: string): string {
  return `A dataset named “${name}” already exists in this project.`;
}

export function isProblem(error: unknown, code: string): boolean {
  return isApiError(error) && error.code === code;
}

const REJECTION_COPY: Record<string, string> = {
  "no payload stored": "No stored payload",
  "no llm span": "No LLM span",
  "trace not found": "Trace not found",
  "span not found": "Span not found in this trace",
};

/** A from-traces rejection reason as a short sentence-case phrase. */
export function rejectionLabel(reason: string): string {
  const known = REJECTION_COPY[reason];
  if (known !== undefined) {
    return known;
  }
  return reason.charAt(0).toUpperCase() + reason.slice(1);
}
