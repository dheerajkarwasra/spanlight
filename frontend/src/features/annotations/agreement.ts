import type { CompletedScore, Score, ScoreConfig } from "@/lib/api";
import {
  formatScoreNumber,
  roundScore,
  toScoreParts,
  type ScoreValueParts,
} from "@/lib/score-format";

/**
 * How the reviewer's scores for an item compare with the evaluator's (spec E2, rulings 2 and 3).
 * An evaluator score is any `eval` score with the config's name; the newest one is compared.
 * Pure functions only.
 */

export type ScoreValue = ScoreValueParts;

export interface AgreementRow {
  name: string;
  you: ScoreValue | null;
  evaluator: ScoreValue | null;
  verdict: "agree" | "disagree" | "differs" | "none";
  /** The absolute difference of two numbers that differ; null otherwise. */
  difference: number | null;
}

/** The values a completion sent, by score name, as the API stores them (yes/no as 1/0). */
export function submittedValues(scores: readonly CompletedScore[]): Record<string, ScoreValue> {
  return Object.fromEntries(
    scores.map((score) => {
      const value = typeof score.value === "boolean" ? Number(score.value) : (score.value ?? null);
      return [score.name, toScoreParts({ value, string_value: score.string_value ?? null })];
    }),
  );
}

function newestEvaluatorScore(scores: readonly Score[], name: string): ScoreValue | null {
  let newest: Score | null = null;
  for (const score of scores) {
    if (
      score.source === "eval" &&
      score.name === name &&
      (newest === null || Date.parse(score.created_at) > Date.parse(newest.created_at))
    ) {
      newest = score;
    }
  }
  return newest === null ? null : toScoreParts(newest);
}

function compare(
  config: ScoreConfig,
  you: ScoreValue | null,
  evaluator: ScoreValue | null,
): Pick<AgreementRow, "verdict" | "difference"> {
  // A config the reviewer left unscored has nothing to compare.
  if (you === null || evaluator === null) {
    return { verdict: "none", difference: null };
  }
  if (config.data_type === "numeric" && you.value !== null && evaluator.value !== null) {
    // No tolerance: 4 against 4.1 differs by 0.1.
    const difference = roundScore(Math.abs(you.value - evaluator.value));
    return difference === 0
      ? { verdict: "agree", difference: null }
      : { verdict: "differs", difference };
  }
  const same = you.value === evaluator.value && you.string_value === evaluator.string_value;
  return { verdict: same ? "agree" : "disagree", difference: null };
}

/** One row per queue config, in the queue's order. */
export function agreementFor(
  configs: readonly ScoreConfig[],
  submitted: Readonly<Record<string, ScoreValue>>,
  traceScores: readonly Score[],
): AgreementRow[] {
  return configs.map((config) => {
    const you = submitted[config.name] ?? null;
    const evaluator = newestEvaluatorScore(traceScores, config.name);
    return { name: config.name, you, evaluator, ...compare(config, you, evaluator) };
  });
}

/** "Agree", "Disagree", "Differs by 1" or "No evaluator score". */
export function verdictText(row: Pick<AgreementRow, "verdict" | "difference">): string {
  switch (row.verdict) {
    case "agree":
      return "Agree";
    case "disagree":
      return "Disagree";
    case "differs":
      return `Differs by ${formatScoreNumber(row.difference ?? 0)}`;
    case "none":
      return "No evaluator score";
  }
}
