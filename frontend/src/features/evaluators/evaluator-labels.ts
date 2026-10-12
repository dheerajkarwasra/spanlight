import type { Evaluator, EvaluatorKind, HeuristicCheck, RuleTarget } from "@/lib/api";

import { EXPECTED_OUTPUT_RULES, type RuleKind } from "./evaluator-schema";

/** Words shown for an evaluator's kind, rule and sampling, and its one-line summary. */

/** Members see evaluators but cannot change them (`evaluators:write` is admin and owner). */
export const EVALUATORS_READ_ONLY =
  "You can view evaluators. Admins can create, edit and disable them.";

const BOOLEAN_RULES = new Set(["regex", "contains", "json_valid", "exact_match"]);

/** Whether the evaluator's scores are Yes/No: the pass/fail rules (spec §8.2). */
export function scoresAreBoolean(evaluator: Evaluator): boolean {
  return evaluator.config.kind === "heuristic" && BOOLEAN_RULES.has(evaluator.config.check.rule);
}

export const KIND_LABELS: Record<EvaluatorKind, string> = {
  heuristic: "Heuristic",
  llm_judge: "LLM judge",
  webhook: "Webhook",
};

export const RULE_LABELS: Record<RuleKind, string> = {
  contains: "Contains",
  regex: "Regex match",
  json_valid: "JSON valid",
  length: "Length",
  exact_match: "Exact match",
  levenshtein: "Levenshtein similarity",
};

/** What each rule does, under the rule select. */
export const RULE_HINTS: Record<RuleKind, string> = {
  contains: "Scores 1 when the text contains the phrase, 0 when it does not.",
  regex: "Scores 1 when the text matches the pattern, 0 when it does not.",
  json_valid: "Scores 1 when the text parses as JSON, 0 when it does not.",
  length: "Scores the length of the text, in characters or words.",
  exact_match: "Compares the output with the expected output of a dataset item.",
  levenshtein: "Scores how close the output is to the expected output, from 0 to 1.",
};

const TARGET_WORDS: Record<RuleTarget, string> = { output: "Output", input: "Input" };

/** "5%", "0.5%", "0% (off)". */
export function samplingLabel(rate: string): string {
  const percent = Number(rate) * 100;
  if (!Number.isFinite(percent)) {
    return rate;
  }
  if (percent === 0) {
    return "0% (off)";
  }
  return `${Number(percent.toFixed(2))}%`;
}

function checkSummary(check: HeuristicCheck): string {
  switch (check.rule) {
    case "regex":
      return `${TARGET_WORDS[check.target ?? "output"]} matches /${check.pattern}/`;
    case "contains":
      return `${TARGET_WORDS[check.target ?? "output"]} contains “${check.needle}”`;
    case "json_valid":
      return `${TARGET_WORDS[check.target ?? "output"]} is valid JSON`;
    case "length":
      return `${TARGET_WORDS[check.target ?? "output"]} length in ${check.unit === "words" ? "words" : "characters"}`;
    case "exact_match":
      return "Exact match against expected output";
    case "levenshtein":
      return "Similarity to expected output";
  }
}

function scopeSummary(evaluator: Evaluator): string {
  const config = evaluator.config;
  if (config.kind === "heuristic" && EXPECTED_OUTPUT_RULES.has(config.check.rule)) {
    return "experiments only";
  }
  const environments = evaluator.filters.environments ?? [];
  return environments.length === 0 ? "all environments" : environments.join(", ");
}

/**
 * The muted line under an evaluator's name (Figma "Evaluators — List"): what it checks or calls,
 * then where it samples. `channelName` names a webhook's channel when it is known.
 */
export function evaluatorSummary(evaluator: Evaluator, channelName: string | null): string {
  const config = evaluator.config;
  const scope = scopeSummary(evaluator);
  if (config.kind === "heuristic") {
    return `${checkSummary(config.check)} · ${scope}`;
  }
  if (config.kind === "webhook") {
    return `POST to ${channelName ?? "a webhook channel"} · ${scope}`;
  }
  return `LLM judge · ${config.model} · ${scope}`;
}

/** How the evaluator's scores are named on traces. */
export function scoreLabel(name: string): string {
  return `Eval · ${name}`;
}
