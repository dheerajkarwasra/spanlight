/**
 * Score wording only the scores screens use: where a score came from, and scores grouped by name.
 * The value formatting every screen shares lives in `lib/score-format`.
 */
import type { Score, ScoreSource } from "@/lib/api";

const SOURCE_LABELS: Record<ScoreSource, string> = {
  api: "API",
  human: "Human",
  eval: "Eval",
};

/** `API`, `Human · Dheeraj`, `Eval · relevance`: where a score came from, and who made it. */
export function sourceLabel(source: ScoreSource, detail: string | null): string {
  const base = SOURCE_LABELS[source];
  return source !== "api" && detail ? `${base} · ${detail}` : base;
}

export interface ScoreGroup {
  name: string;
  scores: Score[];
}

/**
 * Scores grouped by name. The API lists scores latest first, so each group keeps that order and
 * the group scored most recently comes first.
 */
export function groupScoresByName(scores: readonly Score[]): ScoreGroup[] {
  const groups = new Map<string, Score[]>();
  for (const score of scores) {
    const group = groups.get(score.name);
    if (group) {
      group.push(score);
    } else {
      groups.set(score.name, [score]);
    }
  }
  return Array.from(groups, ([name, grouped]) => ({ name, scores: grouped }));
}
