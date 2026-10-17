/**
 * The failure-category convention (spec §5.1): a categorical score config named
 * `failure_category` whose list must hold `none`. Pure constants and checks for its dialog.
 */
import type { ScoreConfig } from "@/lib/api";
import { CATEGORY_LIMIT } from "@/lib/score-format";

/** The score whose categorical config holds the failure categories (spec §5.1). */
export const FAILURE_CATEGORY_SCORE = "failure_category";
export const NONE_CATEGORY = "none";
const MAX_CATEGORIES = 30;

/** A starting list to edit, not a taxonomy: the categories should come from the reviewers' notes. */
export const DEFAULT_FAILURE_CATEGORIES = [
  NONE_CATEGORY,
  "wrong_answer",
  "hallucination",
  "ignored_instructions",
  "format",
  "refusal",
  "tool_misuse",
  "context_loss",
  "truncated",
  "too_slow",
  "other",
];

/** Why `text` can't join the list, or null. */
export function addCategoryProblem(text: string, list: readonly string[]): string | null {
  if (text === "") return "Type a category first.";
  if (Array.from(text).length > CATEGORY_LIMIT)
    return `Use at most ${String(CATEGORY_LIMIT)} characters.`;
  if (list.includes(text)) return `“${text}” is already in the list.`;
  if (list.length >= MAX_CATEGORIES) return `List at most ${String(MAX_CATEGORIES)} categories.`;
  return null;
}

/** The config's list with `none` first if it lacks it, or the starting list. */
export function initialCategories(config: ScoreConfig | undefined): string[] {
  if (!config || config.categories.length === 0) return DEFAULT_FAILURE_CATEGORIES;
  return config.categories.includes(NONE_CATEGORY)
    ? config.categories
    : [NONE_CATEGORY, ...config.categories];
}
