import { errorMessage, isApiError } from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

/** Field messages the API sends for a score that does not fit its config, made readable. */
function fieldText(error: unknown): string | null {
  if (!isApiError(error)) {
    return null;
  }
  const field = error.fieldErrors[0];
  if (field === undefined) {
    return null;
  }
  const message = field.message.charAt(0).toUpperCase() + field.message.slice(1);
  return message.endsWith(".") ? message : `${message}.`;
}

/** Human copy for a failed score `add` or `delete`; never the raw problem code. */
export function scoreErrorMessage(error: unknown, action: "add" | "delete"): string {
  if (isApiError(error)) {
    if (error.code === "VALIDATION_ERROR") {
      return (
        fieldText(error) ?? "The score does not fit its config. Check the value and try again."
      );
    }
    if (error.status === 403) {
      return action === "delete"
        ? "Only the score's author or an admin can delete it."
        : roleBlockedCopy("add scores in this project");
    }
    if (error.status === 404) {
      return "This trace or score no longer exists. Reload the page.";
    }
  }
  return errorMessage(error);
}

/** Human copy for a failed score config write. `name` is the config the dialog was saving. */
export function scoreConfigErrorMessage(error: unknown, name: string): string {
  if (isApiError(error)) {
    switch (error.code) {
      case "NAME_TAKEN":
        return `A score config named “${name}” already exists in this project.`;
      case "SCORE_CONFIG_IN_USE":
        return `Scores use ${name}, so its type cannot change. Keep the type and edit the range or values instead.`;
      case "VALIDATION_ERROR":
        return fieldText(error) ?? "Check the fields and try again.";
    }
    if (error.status === 403) {
      return roleBlockedCopy("change score configs");
    }
    if (error.status === 404) {
      return "This score config no longer exists. Reload the page.";
    }
  }
  return errorMessage(error);
}

/** The score config cannot be deleted because scores carry its name. */
export function isConfigInUse(error: unknown): boolean {
  return isApiError(error) && error.code === "SCORE_CONFIG_IN_USE";
}

/** The name the create dialog sent is taken: the error belongs under the name field. */
export function isNameTaken(error: unknown): boolean {
  return isApiError(error) && error.code === "NAME_TAKEN";
}
