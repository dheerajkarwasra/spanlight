import { z } from "zod";

import type { EvaluatorKind, HeuristicCheck, JudgeTemplateName, RuleTarget } from "@/lib/api";

/*
 * The evaluator form. One flat set of values covers the three kinds, so switching the kind never
 * loses what was typed; `evaluator-values.ts` builds the `EvaluatorConfig` union the API takes. The
 * per-kind rules run in `superRefine`. The bounds mirror the server's (`evals/config.py`,
 * `evals/evaluators/heuristic.py`).
 */

export type RuleKind = HeuristicCheck["rule"];

/** The score name rule: the evaluator's scores carry its name. */
export const NAME_PATTERN = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
export const NAME_HINT =
  "Lowercase letters, digits, _ . and -, starting with a letter or digit. At most 64 characters.";
export const MAX_PATTERN_LENGTH = 500;
export const MAX_JUDGE_TOKENS = 4096;
export const MAX_FILTER_VALUES = 50;
const MAX_FILTER_LENGTH = 200;

/** Spec §11 copy: a judge without a budget. */
export const BUDGET_REQUIRED_COPY =
  "A judge calls a paid model unattended. Set a monthly budget; it never spends more than this.";

/** The built-in judge templates (spec §8.5), offered before the project's own prompts. */
export const JUDGE_TEMPLATES: readonly { value: JudgeTemplateName; label: string }[] = [
  { value: "relevance", label: "Relevance" },
  { value: "faithfulness", label: "Faithfulness" },
  { value: "toxicity", label: "Toxicity" },
  { value: "format_compliance", label: "Format compliance" },
];

/** Rules that compare with an expected output, which live traces lack: they never sample. */
export const EXPECTED_OUTPUT_RULES: ReadonlySet<RuleKind> = new Set(["exact_match", "levenshtein"]);

export interface EvaluatorFormValues {
  name: string;
  kind: EvaluatorKind;
  rule: RuleKind;
  target: RuleTarget;
  pattern: string;
  needle: string;
  /** Regex only: match regardless of case. */
  ignoreCase: boolean;
  /** Contains and exact match. */
  caseSensitive: boolean;
  normalizeWhitespace: boolean;
  unit: "chars" | "words";
  /** A built-in template, or a version of one of the project's chat prompts. */
  judgeSource: "template" | "prompt";
  judgeTemplate: JudgeTemplateName;
  promptName: string;
  promptVersionId: string;
  credentialId: string;
  model: string;
  maxTokens: string;
  /** Empty: not sent, so the provider's default applies (spec §8.5). */
  temperature: string;
  /** Dollars as typed; only a judge has a budget. */
  budget: string;
  channelId: string;
  /** 0 to 100. */
  samplingPercent: number;
  environments: string[];
  models: string[];
  tags: string[];
  /** Comma-separated trace names. */
  traceNames: string;
  minDurationMs: string;
}

export const EMPTY_EVALUATOR_VALUES: EvaluatorFormValues = {
  name: "",
  kind: "heuristic",
  rule: "regex",
  target: "output",
  pattern: "",
  needle: "",
  ignoreCase: false,
  caseSensitive: false,
  normalizeWhitespace: true,
  unit: "chars",
  judgeSource: "template",
  judgeTemplate: "relevance",
  promptName: "",
  promptVersionId: "",
  credentialId: "",
  model: "",
  maxTokens: "1024",
  temperature: "",
  budget: "",
  channelId: "",
  samplingPercent: 100,
  environments: [],
  models: [],
  tags: [],
  traceNames: "",
  minDurationMs: "",
};

/** The case default each rule starts from: contains ignores case, exact match does not. */
export function caseSensitiveDefault(rule: RuleKind): boolean {
  return rule === "exact_match";
}

/** Why a typed dollar amount can't be a budget, or null. At most 4 decimals, above 0. */
export function budgetProblem(amount: string): string | null {
  const trimmed = amount.trim();
  if (trimmed === "") {
    return BUDGET_REQUIRED_COPY;
  }
  const match = /^(\d*)\.?(\d*)$/.exec(trimmed);
  if (!match || trimmed === ".") {
    return "Enter the budget as a number, for example 0.50.";
  }
  const [, whole = "", fraction = ""] = match;
  if (fraction.length > 4) {
    return "Use at most 4 decimal places.";
  }
  if (whole.replace(/^0+/, "").length > 8) {
    return "Use a budget below $100,000,000.";
  }
  return /[1-9]/.test(trimmed) ? null : "Enter a budget greater than 0.";
}

/** Comma-separated names, trimmed and de-duplicated. */
export function splitNames(text: string): string[] {
  return [
    ...new Set(
      text
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean),
    ),
  ];
}

type Issue = { path: keyof EvaluatorFormValues; message: string };

function heuristicIssues(values: EvaluatorFormValues): Issue[] {
  const issues: Issue[] = [];
  if (values.rule === "regex" && values.pattern.trim() === "") {
    issues.push({ path: "pattern", message: "Enter a pattern." });
  }
  if (values.rule === "contains" && values.needle === "") {
    issues.push({ path: "needle", message: "Enter the text to look for." });
  }
  if (EXPECTED_OUTPUT_RULES.has(values.rule) && values.samplingPercent > 0) {
    issues.push({
      path: "samplingPercent",
      message: "This rule needs an expected output, which live traces lack. Set sampling to 0.",
    });
  }
  return issues;
}

function judgeIssues(values: EvaluatorFormValues): Issue[] {
  const issues: Issue[] = [];
  if (values.judgeSource === "prompt" && values.promptVersionId === "") {
    issues.push({ path: "promptVersionId", message: "Pick one of your prompts and its version." });
  }
  if (values.credentialId === "") {
    issues.push({ path: "credentialId", message: "Pick an Anthropic or OpenAI credential." });
  }
  if (values.model.trim() === "") {
    issues.push({ path: "model", message: "Enter the model the judge calls." });
  }
  const tokens = Number(values.maxTokens);
  if (!/^\d+$/.test(values.maxTokens) || tokens < 1 || tokens > MAX_JUDGE_TOKENS) {
    issues.push({
      path: "maxTokens",
      message: `Enter a whole number from 1 to ${MAX_JUDGE_TOKENS}.`,
    });
  }
  const temperature = values.temperature.trim();
  if (temperature !== "" && !/^(2(\.0{1,2})?|[01](\.\d{1,2})?|\.\d{1,2})$/.test(temperature)) {
    issues.push({ path: "temperature", message: "Enter 0 to 2 with at most 2 decimals." });
  }
  const budget = budgetProblem(values.budget);
  if (budget) {
    issues.push({ path: "budget", message: budget });
  }
  return issues;
}

function filterIssues(values: EvaluatorFormValues): Issue[] {
  const issues: Issue[] = [];
  const names = splitNames(values.traceNames);
  if (names.length > MAX_FILTER_VALUES || names.some((name) => name.length > MAX_FILTER_LENGTH)) {
    issues.push({
      path: "traceNames",
      message: `Use at most ${MAX_FILTER_VALUES} names of ${MAX_FILTER_LENGTH} characters or fewer.`,
    });
  }
  if (values.minDurationMs.trim() !== "" && !/^\d+$/.test(values.minDurationMs.trim())) {
    issues.push({ path: "minDurationMs", message: "Enter a whole number of milliseconds." });
  }
  return issues;
}

export const evaluatorFormSchema = z
  .object({
    name: z.string().trim().regex(NAME_PATTERN, NAME_HINT),
    kind: z.enum(["heuristic", "llm_judge", "webhook"]),
    rule: z.enum(["regex", "json_valid", "length", "contains", "exact_match", "levenshtein"]),
    target: z.enum(["output", "input"]),
    pattern: z.string().max(MAX_PATTERN_LENGTH, `Use ${MAX_PATTERN_LENGTH} characters or fewer.`),
    needle: z.string().max(MAX_PATTERN_LENGTH, `Use ${MAX_PATTERN_LENGTH} characters or fewer.`),
    ignoreCase: z.boolean(),
    caseSensitive: z.boolean(),
    normalizeWhitespace: z.boolean(),
    unit: z.enum(["chars", "words"]),
    judgeSource: z.enum(["template", "prompt"]),
    judgeTemplate: z.enum(["relevance", "faithfulness", "toxicity", "format_compliance"]),
    promptName: z.string(),
    promptVersionId: z.string(),
    credentialId: z.string(),
    model: z.string().trim().max(200, "Use 200 characters or fewer."),
    maxTokens: z.string().trim(),
    temperature: z.string(),
    budget: z.string(),
    channelId: z.string(),
    samplingPercent: z.number().min(0).max(100),
    environments: z.array(z.string()).max(MAX_FILTER_VALUES),
    models: z.array(z.string()).max(MAX_FILTER_VALUES),
    tags: z.array(z.string()).max(MAX_FILTER_VALUES),
    traceNames: z.string(),
    minDurationMs: z.string(),
  })
  .superRefine((values, context) => {
    const kindIssues =
      values.kind === "heuristic"
        ? heuristicIssues(values)
        : values.kind === "llm_judge"
          ? judgeIssues(values)
          : values.channelId === ""
            ? [{ path: "channelId" as const, message: "Pick a webhook channel." }]
            : [];
    for (const issue of [...kindIssues, ...filterIssues(values)]) {
      context.addIssue({ code: "custom", path: [issue.path], message: issue.message });
    }
  });
