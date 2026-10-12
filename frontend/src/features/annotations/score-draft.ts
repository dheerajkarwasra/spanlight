import type { CompletedScore, ScoreConfig } from "@/lib/api";
import { checkScoreInput, COMMENT_LIMIT, formatScoreNumber, roundScore } from "@/lib/score-format";

/**
 * The reviewer's unsaved scores for one item, keyed by score name: the typed text of a number
 * score, the picked option of a yes/no or category score. Pure functions only.
 */
export type ScoreDraft = Readonly<Record<string, string | null>>;

/** The values a yes/no score offers, in key order (`1` = Yes, `2` = No). */
export const BOOLEAN_OPTIONS = ["true", "false"] as const;

export function emptyDraft(configs: readonly ScoreConfig[]): ScoreDraft {
  return Object.fromEntries(configs.map((config) => [config.name, null]));
}

/** The options keys `1`–`9` pick from, or null for a number or free-text score. */
export function choicesOf(config: ScoreConfig): readonly string[] | null {
  if (config.data_type === "boolean") {
    return BOOLEAN_OPTIONS;
  }
  if (config.data_type === "categorical" && config.categories.length > 0) {
    return config.categories;
  }
  return null;
}

export function optionLabel(config: ScoreConfig, option: string): string {
  if (config.data_type !== "boolean") {
    return option;
  }
  return option === "true" ? "Yes" : "No";
}

/** Quick-fill values for a bounded number score: the bounds and their midpoint. */
export function quickValues(config: ScoreConfig): string[] {
  const { min_value: min, max_value: max } = config;
  if (config.data_type !== "numeric" || min === null || max === null || max <= min) {
    return [];
  }
  return [min, roundScore((min + max) / 2), max].map((value) => formatScoreNumber(value));
}

/**
 * Why a typed or picked value does not fit its config, with the API's rules (four decimals,
 * the range, at most 200 characters for a category); null when it fits or nothing is set yet.
 */
export function inputProblem(config: ScoreConfig, text: string | null): string | null {
  if (text === null || text.trim() === "") {
    return null;
  }
  const check = checkScoreInput(config, text);
  return check.ok ? null : check.error;
}

function isSet(config: ScoreConfig, draft: ScoreDraft): boolean {
  const value = draft[config.name] ?? null;
  return value !== null && value.trim() !== "" && checkScoreInput(config, value).ok;
}

/** The scores still to set, in queue order. Every score of a queue is required. */
export function missingScores(configs: readonly ScoreConfig[], draft: ScoreDraft): string[] {
  return configs.filter((config) => !isSet(config, draft)).map((config) => config.name);
}

export function setCount(configs: readonly ScoreConfig[], draft: ScoreDraft): number {
  return configs.length - missingScores(configs, draft).length;
}

/**
 * The scores `complete` sends, or null while any score is unset or invalid. The comment rides on
 * the first score so it is stored once.
 */
export function toCompletedScores(
  configs: readonly ScoreConfig[],
  draft: ScoreDraft,
  comment: string,
): CompletedScore[] | null {
  if (configs.length === 0 || missingScores(configs, draft).length > 0) {
    return null;
  }
  const note = comment.trim() === "" ? null : comment.trim().slice(0, COMMENT_LIMIT);
  const scores: CompletedScore[] = [];
  for (const [index, config] of configs.entries()) {
    // Checked again so the values sent are the ones the API stores: numbers rounded to four
    // decimals, categories trimmed.
    const check = checkScoreInput(config, draft[config.name] ?? "");
    if (!check.ok) {
      return null;
    }
    scores.push({ name: config.name, ...check.input, comment: index === 0 ? note : null });
  }
  return scores;
}

/**
 * The score keys `1`–`9` act on: the focused category or yes/no score, else the first one still
 * unset. Null when there is none, so a stray digit changes nothing.
 */
export function activeChoiceScore(
  configs: readonly ScoreConfig[],
  draft: ScoreDraft,
  focusedName: string | null,
): ScoreConfig | null {
  const choices = configs.filter((config) => choicesOf(config) !== null);
  const focused = choices.find((config) => config.name === focusedName);
  if (focused !== undefined) {
    return focused;
  }
  return choices.find((config) => (draft[config.name] ?? null) === null) ?? null;
}

const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight"];

/** The status line under the actions: what is left before Submit unlocks. */
export function draftStatus(configs: readonly ScoreConfig[], draft: ScoreDraft): string {
  const missing = missingScores(configs, draft);
  if (missing.length === 0) {
    return "All scores set. Submit to save them and open the next item.";
  }
  const total = NUMBER_WORDS[configs.length] ?? String(configs.length);
  const left = `${String(missing.length)} ${missing.length === 1 ? "score" : "scores"} left`;
  const all = configs.length === 1 ? "it is" : `all ${total} are`;
  return `${left}: ${missing.join(", ")}. Submit unlocks when ${all} set.`;
}
