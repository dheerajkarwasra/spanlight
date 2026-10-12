import { errorMessage, isApiError } from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

import type { WorkNotice } from "./work-state";

/** The scores a `SCORE_REQUIRED` or `SCORE_NOT_IN_QUEUE` problem names (`scores.<name>`). */
function scoreNames(error: unknown): string {
  if (!isApiError(error)) {
    return "";
  }
  return error.fieldErrors
    .map((field) => field.field.replace(/^scores\./, ""))
    .filter((name) => name !== "")
    .join(", ");
}

const QUEUE_COPY: Record<string, string> = {
  NAME_TAKEN: "A queue with this name already exists in this project. Pick another name.",
  UNKNOWN_SCORE_CONFIG: "One of the chosen scores no longer exists. Reload and pick again.",
  FORBIDDEN: roleBlockedCopy("change review queues"),
};

/** Human copy for a failed queue write (create, delete, add items); never the raw code. */
export function queueErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    const copy = QUEUE_COPY[error.code];
    if (copy !== undefined) {
      return copy;
    }
    if (error.status === 404) {
      return "This queue no longer exists. Go back to the queue list.";
    }
  }
  return errorMessage(error);
}

/** The trace ids an add-items request named that the project does not have. */
export function unknownTraceIds(error: unknown): string[] {
  if (!isApiError(error) || error.code !== "VALIDATION_ERROR") {
    return [];
  }
  return error.fieldErrors
    .filter((field) => field.field === "trace_ids")
    .map((field) => field.message.replace(/^unknown trace /, ""));
}

/** The item is no longer the reviewer's to finish, so the next one should load. */
export function isClaimLost(error: unknown): boolean {
  return (
    isApiError(error) &&
    (error.code === "ITEM_NOT_ASSIGNED_TO_YOU" || error.code === "ITEM_NOT_PENDING")
  );
}

/** What a failed complete, skip or release tells the reviewer. */
export function workNotice(error: unknown): WorkNotice {
  if (isApiError(error)) {
    switch (error.code) {
      case "ITEM_NOT_ASSIGNED_TO_YOU":
        return {
          title: "This item was reassigned",
          message:
            "This item was reassigned to someone else, so your scores were not saved. The next item is loading.",
        };
      case "ITEM_NOT_PENDING":
        return {
          title: "This item is already finished",
          message:
            "Someone else finished this item, so your scores were not saved. The next item is loading.",
        };
      case "SCORE_REQUIRED":
        return {
          title: "A score is missing",
          message: `Every score of this queue is required. Set ${scoreNames(error) || "them all"} and submit again.`,
        };
      case "SCORE_NOT_IN_QUEUE":
        return {
          title: "The queue's scores changed",
          message: `${scoreNames(error) || "A score"} is no longer part of this queue. Reload the page to see the current list.`,
        };
      case "VALIDATION_ERROR":
        return { title: "A score does not fit", message: validationText(error) };
    }
  }
  return { title: "Couldn't save", message: errorMessage(error) };
}

/** The server's reason without its field path ("value: must be …" → "Must be …"). */
function validationText(error: unknown): string {
  if (!isApiError(error)) {
    return errorMessage(error);
  }
  const reason = error.fieldErrors[0]?.message.trim() ?? "";
  if (reason === "") {
    return "A score does not fit its config. Check the values and submit again.";
  }
  const sentence = reason.charAt(0).toUpperCase() + reason.slice(1);
  return `${sentence}${sentence.endsWith(".") ? "" : "."} Fix the score and submit again.`;
}
