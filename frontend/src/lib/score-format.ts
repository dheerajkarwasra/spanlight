/**
 * How scores and score configs read on every screen (traces, review queues, evaluators,
 * experiments), and how a hand-typed score is checked against its config before it is sent, with
 * the rules the API applies. Pure functions only.
 */
import type { ScoreConfig, ScoreCreate, ScoreDataType } from "@/lib/api";

/** The API's limits: values within ±1e9, comments up to 2,000 characters, categories up to 200. */
export const VALUE_LIMIT = 1e9;
export const COMMENT_LIMIT = 2000;
export const CATEGORY_LIMIT = 200;

/** The value part of a score, as a score, a summary in the trace list or a draft carries it. */
export interface ScoreValueParts {
  value: number | null;
  string_value: string | null;
}

/** The API keeps four decimals of a score and checks its range on the rounded value. */
export function roundScore(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

/** A number with at most four decimals and no trailing zeros: `0.8235`, `0.7`, `3`. */
export function formatScoreNumber(value: number): string {
  return String(roundScore(value));
}

/** A score value as the API sends it: a number, or a decimal string on evaluator runs. */
export function toScoreParts(score: {
  value: number | string | null;
  string_value: string | null;
}): ScoreValueParts {
  const value = typeof score.value === "string" ? Number(score.value) : score.value;
  return { value, string_value: score.string_value };
}

/**
 * A score's value: a number with up to four decimals, `Yes`/`No` for a boolean, a category as
 * written. Without a known type, a value is shown as a number.
 */
export function formatScoreValue(score: ScoreValueParts, dataType: ScoreDataType | null): string {
  if (score.string_value !== null) {
    return score.string_value;
  }
  if (score.value === null || !Number.isFinite(score.value)) {
    return "—";
  }
  if (dataType === "boolean") {
    return score.value === 1 ? "Yes" : "No";
  }
  return formatScoreNumber(score.value);
}

/** "0 to 1", "1 or more", "any number": a numeric config's range. */
export function rangeText(config: ScoreConfig): string {
  const { min_value: min, max_value: max } = config;
  if (min !== null && max !== null) {
    return `${formatScoreNumber(min)} to ${formatScoreNumber(max)}`;
  }
  if (min !== null) {
    return `${formatScoreNumber(min)} or more`;
  }
  if (max !== null) {
    return `${formatScoreNumber(max)} or less`;
  }
  return "any number";
}

/** The values a config allows: "0 to 1", "yes or no", "good, ok, bad" or "any value". */
export function allowedValuesText(config: ScoreConfig): string {
  if (config.data_type === "numeric") {
    return rangeText(config);
  }
  if (config.data_type === "boolean") {
    return "yes or no";
  }
  return config.categories.length > 0 ? config.categories.join(", ") : "any value";
}

/** The chip beside a score name: "numeric · 0 to 1", "yes or no", "categorical · good, ok, bad". */
export function configChipText(config: ScoreConfig): string {
  if (config.data_type === "boolean") {
    return "yes or no";
  }
  if (config.data_type === "categorical" && config.categories.length === 0) {
    return "categorical";
  }
  return `${config.data_type} · ${allowedValuesText(config)}`;
}

export type ScoreInputCheck =
  { ok: true; input: Pick<ScoreCreate, "value" | "string_value"> } | { ok: false; error: string };

function numericCheck(config: ScoreConfig, raw: string): ScoreInputCheck {
  const text = raw.trim();
  const typed = Number(text);
  if (text === "" || !Number.isFinite(typed)) {
    return { ok: false, error: "Enter a number." };
  }
  const value = roundScore(typed);
  const { min_value: min, max_value: max } = config;
  const outside =
    Math.abs(value) > VALUE_LIMIT || (min !== null && value < min) || (max !== null && value > max);
  if (!outside) {
    return { ok: true, input: { value } };
  }
  if (min !== null && max !== null) {
    return {
      ok: false,
      error: `Enter a number from ${formatScoreNumber(min)} to ${formatScoreNumber(max)}.`,
    };
  }
  if (min !== null) {
    return { ok: false, error: `Enter a number of ${formatScoreNumber(min)} or more.` };
  }
  if (max !== null) {
    return { ok: false, error: `Enter a number of ${formatScoreNumber(max)} or less.` };
  }
  return { ok: false, error: "Enter a number between -1,000,000,000 and 1,000,000,000." };
}

function categoricalCheck(config: ScoreConfig, raw: string): ScoreInputCheck {
  const text = raw.trim();
  if (config.categories.length > 0) {
    return config.categories.includes(text)
      ? { ok: true, input: { string_value: text } }
      : { ok: false, error: `Pick one of ${config.categories.join(", ")}.` };
  }
  if (text === "") {
    return { ok: false, error: "Enter a value." };
  }
  if (text.length > CATEGORY_LIMIT) {
    return { ok: false, error: `Use at most ${String(CATEGORY_LIMIT)} characters.` };
  }
  return { ok: true, input: { string_value: text } };
}

/**
 * Checks a typed or picked value against its config, with the same rules the API applies. A
 * boolean draft is `"true"` or `"false"`; a number is typed text; a category is the option.
 */
export function checkScoreInput(config: ScoreConfig, raw: string): ScoreInputCheck {
  if (config.data_type === "numeric") {
    return numericCheck(config, raw);
  }
  if (config.data_type === "boolean") {
    return raw === "true" || raw === "false"
      ? { ok: true, input: { value: raw === "true" } }
      : { ok: false, error: "Pick Yes or No." };
  }
  return categoricalCheck(config, raw);
}
