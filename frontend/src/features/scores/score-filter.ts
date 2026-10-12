/**
 * The explorer's score facet: a score name, a comparator and a value, kept in the trace list's
 * URL as `score`, `score_min`, `score_max` and `score_value`. Pure functions only.
 *
 * A boolean score is stored as 1 or 0, so "format = Yes" travels as `score_min=1&score_max=1`.
 */
import type { ScoreDataType } from "@/lib/api";
import { formatScoreNumber } from "@/lib/score-format";

export type ScoreComparator = "gte" | "lte" | "eq";

export const COMPARATOR_SYMBOLS: Record<ScoreComparator, string> = {
  gte: "≥",
  lte: "≤",
  eq: "=",
};

export const COMPARATOR_LABELS: Record<ScoreComparator, string> = {
  gte: "At least",
  lte: "At most",
  eq: "Equal to",
};

/** The score filter as the URL holds it. */
export interface ScoreFilterParams {
  score?: string | undefined;
  score_min?: number | undefined;
  score_max?: number | undefined;
  score_value?: string | undefined;
}

/** What the facet's form edits: the value is typed text, `"true"`/`"false"`, or a category. */
export interface ScoreFilterDraft {
  name: string;
  comparator: ScoreComparator;
  value: string;
}

export const CLEARED_SCORE_FILTER: Record<keyof ScoreFilterParams, undefined> = {
  score: undefined,
  score_min: undefined,
  score_max: undefined,
  score_value: undefined,
};

/** Yes is stored as 1 and No as 0; anything else is no value rather than a silent No. */
function booleanBit(value: string): 0 | 1 | null {
  if (value === "true" || value === "1") {
    return 1;
  }
  if (value === "false" || value === "0") {
    return 0;
  }
  return null;
}

/** The URL params for a draft. A name without a value keeps every trace with that score. */
export function toScoreFilterParams(
  draft: ScoreFilterDraft,
  dataType: ScoreDataType | null,
): ScoreFilterParams {
  const name = draft.name.trim();
  const value = draft.value.trim();
  if (name === "") {
    return { ...CLEARED_SCORE_FILTER };
  }
  const params: ScoreFilterParams = { ...CLEARED_SCORE_FILTER, score: name };
  if (value === "") {
    return params;
  }
  if (dataType === "categorical") {
    return { ...params, score_value: value };
  }
  if (dataType === "boolean") {
    const bit = booleanBit(value);
    return bit === null ? params : { ...params, score_min: bit, score_max: bit };
  }
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return params;
  }
  return {
    ...params,
    score_min: draft.comparator === "lte" ? undefined : number,
    score_max: draft.comparator === "gte" ? undefined : number,
  };
}

/** The draft a set of URL params reopens the facet with. */
export function scoreFilterDraft(
  params: ScoreFilterParams,
  dataType: ScoreDataType | null,
): ScoreFilterDraft {
  const name = params.score ?? "";
  const { score_min: min, score_max: max } = params;
  if (params.score_value !== undefined) {
    return { name, comparator: "eq", value: params.score_value };
  }
  if (min !== undefined && max !== undefined && min === max) {
    const value = dataType === "boolean" ? String(min === 1) : String(min);
    return { name, comparator: "eq", value };
  }
  if (max !== undefined) {
    return { name, comparator: "lte", value: String(max) };
  }
  return { name, comparator: "gte", value: min === undefined ? "" : String(min) };
}

/** The applied chip's text: "relevance ≥ 0.8", "format = Yes", "tone = ok", "relevance". */
export function scoreFilterText(
  params: ScoreFilterParams,
  dataType: ScoreDataType | null,
): string | null {
  const name = params.score;
  if (name === undefined || name === "") {
    return null;
  }
  const { score_min: min, score_max: max } = params;
  if (params.score_value !== undefined) {
    return `${name} = ${params.score_value}`;
  }
  if (min !== undefined && max !== undefined) {
    if (min === max) {
      const shown = dataType === "boolean" ? (min === 1 ? "Yes" : "No") : formatScoreNumber(min);
      return `${name} = ${shown}`;
    }
    return `${name} ${formatScoreNumber(min)} to ${formatScoreNumber(max)}`;
  }
  if (min !== undefined) {
    return `${name} ≥ ${formatScoreNumber(min)}`;
  }
  if (max !== undefined) {
    return `${name} ≤ ${formatScoreNumber(max)}`;
  }
  return name;
}
