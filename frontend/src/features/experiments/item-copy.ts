/** How a dataset item and an item's failure read on the run and compare screens. Pure. */
import { TEMPLATE_TOO_LARGE_MESSAGE } from "@/lib/prompt-template";

const MAX_INPUT_CHARS = 280;

/** `"<status> <code>: <message>"`, the shape the gateway writes for a provider failure. */
const PROVIDER_ERROR = /^(\d{3}) ([\w.-]+): ([\s\S]*)$/;

function clip(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > MAX_INPUT_CHARS ? `${flat.slice(0, MAX_INPUT_CHARS - 1)}…` : flat;
}

/**
 * One line for a dataset item's input: the text itself, the values of a variables object joined,
 * or compact JSON for anything else. Null when the input is unknown (not loaded).
 */
export function inputText(input: unknown): string | null {
  if (input === undefined) {
    return null;
  }
  if (typeof input === "string") {
    return clip(input);
  }
  if (input !== null && typeof input === "object" && !Array.isArray(input)) {
    const values = Object.values(input);
    if (values.length > 0 && values.every((value) => typeof value === "string")) {
      return clip(values.join(" · "));
    }
  }
  // `JSON.stringify` returns undefined at runtime for values JSON cannot hold.
  const json = JSON.stringify(input) as string | undefined;
  return clip(json ?? "");
}

/** "#32": an item's place in dataset order, or the start of its id when the place is unknown. */
export function itemLabel(itemId: string, position: number | undefined): string {
  return position === undefined ? itemId.slice(0, 8) : `#${position}`;
}

export interface ItemProblem {
  /** The sentence shown to people. */
  text: string;
  /** A stable short label shown in mono under it, or null when there is no honest one. */
  code: string | null;
}

/** Human copy for an item's `error`. Never shows a raw code on its own. */
export function itemProblem(error: string): ItemProblem {
  if (error.startsWith("missing_variables:")) {
    const names = error.slice("missing_variables:".length).trim();
    const several = names.includes(",");
    return {
      text: `Missing ${several ? "variables" : "variable"}: ${names}`,
      code: "missing_variables",
    };
  }
  if (error === "prompt_too_large") {
    return { text: TEMPLATE_TOO_LARGE_MESSAGE, code: "prompt_too_large" };
  }
  if (error === "BUDGET_EXCEEDED") {
    return { text: "Skipped: a budget on this project blocks the call.", code: "BUDGET_EXCEEDED" };
  }
  if (error === "internal_error") {
    return { text: "Something went wrong on our side running this item.", code: null };
  }
  if (error === "cancelled") {
    return { text: "Cancelled when the run stopped.", code: null };
  }
  const provider = PROVIDER_ERROR.exec(error);
  if (provider) {
    const status = Number(provider[1]);
    if (status === 429) {
      return { text: "The provider rate limited this request.", code: "RATE_LIMITED" };
    }
    if (status === 401 || status === 403) {
      return { text: "The provider did not accept this credential.", code: null };
    }
    const message = (provider[3] ?? "").trim();
    return { text: message || "The provider could not answer.", code: null };
  }
  return { text: error.trim() || "The provider could not answer.", code: null };
}

/** Why an item has no value for a score that other items have. */
export function missingScoreReason(evaluatorError: string | null): string {
  return evaluatorError
    ? "No score: scoring this item failed. The answer and its cost are kept."
    : "No score: this evaluator's monthly budget was spent, so it skipped the item.";
}
